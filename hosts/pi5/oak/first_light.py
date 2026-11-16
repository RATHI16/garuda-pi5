#!/usr/bin/env python3
"""Bounded read-only OAK first light. No EEPROM writes, IR, NN, or FC commands."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import subprocess
import time

import depthai as dai


def command(*args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=5)
    return {"rc": p.returncode, "out": p.stdout.strip(), "err": p.stderr.strip()}


def cpu_ticks():
    return {p[0]: list(map(int, p[1:9])) for line in Path('/proc/stat').read_text().splitlines()
            if (p := line.split()) and p[0].startswith('cpu')}


def cpu_usage(before, after):
    result = {}
    for core, ticks in after.items():
        delta = [b-a for a, b in zip(before[core], ticks)]
        total = sum(delta)
        result[core] = 100*(total-delta[3]-delta[4])/total if total else None
    return result


def seconds(stamp):
    return stamp.total_seconds()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True)
    ap.add_argument('--seconds', type=float, default=30)
    args = ap.parse_args()
    if not 1 <= args.seconds <= 60:
        ap.error('first-light duration must be 1..60 seconds')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    summary = {"sdk": dai.__version__, "numpy": importlib.metadata.version('numpy'),
               "duration_requested_s": args.seconds, "ir": "explicitly disabled",
               "power_before": command('vcgencmd', 'get_throttled'),
               "temp_before": command('vcgencmd', 'measure_temp'),
               "config": {"left_right": [640, 400, 30], "rgb": [1280, 720, 30],
                          "accel_requested_hz": 400, "gyro_requested_hz": 400},
               "streams": {}, "cpu_intervals": []}
    try:
        with dai.Device() as dev:
            dev.setIrLaserDotProjectorIntensity(0)
            dev.setIrFloodLightIntensity(0)
            for method in ['getDeviceInfo', 'getUsbSpeed', 'getConnectedIMU',
                           'getCameraSensorNames', 'getBootloaderVersion',
                           'getIMUFirmwareVersion', 'getOSVersion']:
                try:
                    value = getattr(dev, method)()
                    summary[method] = None if value is None else str(value)
                except Exception as exc:
                    summary[method] = {"unavailable": str(exc)}
            if dev.getUsbSpeed() not in (dai.UsbSpeed.SUPER, dai.UsbSpeed.SUPER_PLUS):
                raise RuntimeError('USB3 required; refusing multi-stream test')
            cal = dev.readCalibration()
            (out/'calibration.json').write_text(json.dumps(cal.eepromToJson(), indent=2))
            summary['baseline_cm_calibrated'] = cal.getBaselineDistance(
                dai.CameraBoardSocket.CAM_B, dai.CameraBoardSocket.CAM_C, False)
            summary['fov_degrees'] = {
                socket.name: {"factory_nominal": cal.getFov(socket, True),
                              "intrinsics_derived": cal.getFov(socket, False)}
                for socket in dev.getConnectedCameras()}
            with dai.Pipeline(dev) as pipeline:
                queues = {}
                for name, socket, size, fmt in [
                    ('left', dai.CameraBoardSocket.CAM_B, (640, 400), dai.ImgFrame.Type.GRAY8),
                    ('right', dai.CameraBoardSocket.CAM_C, (640, 400), dai.ImgFrame.Type.GRAY8),
                    ('rgb', dai.CameraBoardSocket.CAM_A, (1280, 720), dai.ImgFrame.Type.BGR888i)]:
                    camera = pipeline.create(dai.node.Camera).build(socket)
                    queues[name] = camera.requestOutput(size, type=fmt, fps=30,
                        enableUndistortion=False).createOutputQueue(maxSize=8, blocking=False)
                imu = pipeline.create(dai.node.IMU)
                imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW,
                                     dai.IMUSensor.GYROSCOPE_RAW], 400)
                imu.setBatchReportThreshold(1)
                imu.setMaxBatchReports(10)
                queues['imu'] = imu.out.createOutputQueue(maxSize=50, blocking=False)
                pipeline.start()
                start = last_health = time.monotonic()
                last_cpu = cpu_ticks()
                process_start = time.process_time()
                with (out/'samples.jsonl').open('w') as samples:
                    while time.monotonic()-start < args.seconds and pipeline.isRunning():
                        did_work = False
                        for name, queue in queues.items():
                            packet = queue.tryGet()
                            if packet is None:
                                continue
                            did_work = True
                            arrival = time.monotonic()
                            if name == 'imu':
                                reports = [(kind, report) for p in packet.packets for kind, report in
                                           [('accel', p.acceleroMeter), ('gyro', p.gyroscope)]]
                            else:
                                reports = [(name, packet)]
                                if name not in summary['streams']:
                                    data = packet.getData().tobytes()
                                    if name == 'rgb':
                                        import numpy as np
                                        data = np.frombuffer(data, dtype=np.uint8).reshape(
                                            packet.getHeight(), packet.getWidth(), 3)[:, :, ::-1].tobytes()
                                    magic = 'P6' if name == 'rgb' else 'P5'
                                    ext = 'ppm' if name == 'rgb' else 'pgm'
                                    (out/f'{name}.{ext}').write_bytes(
                                        f'{magic}\n{packet.getWidth()} {packet.getHeight()}\n255\n'.encode()+data)
                            for stream, report in reports:
                                seq = report.getSequenceNum() if name != 'imu' else report.sequence
                                stamp = seconds(report.getTimestamp())
                                device_stamp = seconds(report.getTimestampDevice())
                                row = {"stream": stream, "seq": seq, "host_synced_s": stamp,
                                       "device_s": device_stamp, "arrival_monotonic_s": arrival}
                                if name == 'imu':
                                    row['xyz'] = [report.x, report.y, report.z]
                                samples.write(json.dumps(row)+'\n')
                                stats = summary['streams'].setdefault(stream, {
                                    'count': 0, 'sequence_gaps': 0, 'duplicates': 0,
                                    'out_of_order': 0, 'first_timestamp': stamp, 'first_arrival': arrival})
                                if stats['count']:
                                    diff = seq-stats['last_seq']
                                    if diff == 0:
                                        stats['duplicates'] += 1
                                        # IMU batching can repeat a report paired with a newer other sensor.
                                        continue
                                    if diff < 0:
                                        stats['out_of_order'] += 1
                                    stats['sequence_gaps'] += max(0, diff-1)
                                stats['count'] += 1
                                stats.update(last_seq=seq, last_timestamp=stamp, last_arrival=arrival)
                        now = time.monotonic()
                        if now-last_health >= 1:
                            ticks = cpu_ticks()
                            health = {'elapsed_s': now-start, 'cpu_percent': cpu_usage(last_cpu, ticks),
                                      'throttled': command('vcgencmd', 'get_throttled'),
                                      'temperature': command('vcgencmd', 'measure_temp')}
                            summary['cpu_intervals'].append(health)
                            last_cpu, last_health = ticks, now
                            flags = int(health['throttled']['out'].split('=')[-1], 16)
                            if flags & 0xf:
                                raise RuntimeError('Active undervoltage/frequency/thermal limit; ending test')
                        if not did_work:
                            time.sleep(.001)
                summary['elapsed_s'] = time.monotonic()-start
                summary['process_cpu_percent_one_core'] = 100*(time.process_time()-process_start)/summary['elapsed_s']
                for stats in summary['streams'].values():
                    elapsed = stats['last_timestamp']-stats['first_timestamp']
                    stats['unique_timestamp_rate_hz'] = (stats['count']-1)/elapsed if elapsed > 0 else None
                    stats['received_per_wall_second'] = stats['count']/summary['elapsed_s']
                summary['completed'] = all(summary['streams'].get(s, {}).get('count', 0)>1
                                           for s in ['left','right','rgb','accel','gyro'])
    except Exception as exc:
        summary.update(completed=False, error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        summary['power_after'] = command('vcgencmd', 'get_throttled')
        summary['temp_after'] = command('vcgencmd', 'measure_temp')
        (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
