#!/usr/bin/env python3
"""Cost of OAK mono capture at 640x400, 1280x800, or both. Capture only.

No rectification, matching, detection or encoding runs here, so the figures are the
cost of receiving frames and nothing else. Not a depth or accuracy test.
The caller must hold the Pi coordination lock and own the camera.
"""
import argparse,json,os,platform,resource,subprocess,time
from pathlib import Path
import numpy as np

SMALL=(640,400);FULL=(1280,800)
CONFIGS={'small':[SMALL],'full':[FULL],'both':[SMALL,FULL]}

def stream_names(config):
    return ['rgb']+[f'{side}_{w}x{h}' for side in ('left','right') for w,h in CONFIGS[config]]

def cpu_ticks():
    f=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
    return sum(f),f[3]+f[4]

def thread_ticks():
    """CPU ticks per thread of this process, keyed by (tid, name)."""
    out={}
    for t in Path('/proc/self/task').iterdir():
        try:f=(t/'stat').read_text().rsplit(')',1)[1].split();out[(t.name,(t/'comm').read_text().strip()+(' (main loop)' if t.name==str(os.getpid()) else ''))]=int(f[11])+int(f[12])
        except OSError:pass
    return out

def thread_cores(before,after,wall,hz=100):
    """Cores used per thread name over the window, largest first. Pure; tested offline."""
    total={}
    for key,ticks in after.items():
        total[key[1]]=total.get(key[1],0)+(ticks-before.get(key,0))/hz/wall
    return {k:round(v,4) for k,v in sorted(total.items(),key=lambda x:-x[1]) if v>=.005}

def cpu_mhz():
    try:return [int(p.read_text())/1000 for p in sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq'))]
    except OSError:return None

def temperature():
    return int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000

def throttled():
    try:return subprocess.run(['vcgencmd','get_throttled'],capture_output=True,text=True,timeout=5).stdout.strip()
    except Exception as exc:return 'unavailable: '+str(exc)

def percentiles(values):
    if len(values)==0:return None
    a=np.asarray(values,dtype=np.float64)
    return dict(n=int(a.size),p50=float(np.percentile(a,50)),p99=float(np.percentile(a,99)),max=float(a.max()))

def summarise(rows,settle_ns,wall_s):
    """rows: stream -> list of (seq, device_ns, host_ns, dequeue_ns, bytes). Pure; tested offline."""
    out={}
    for name,items in rows.items():
        kept=[r for r in items if r[3]>=settle_ns]
        seqs=[r[0] for r in kept];device=np.array([r[1] for r in kept],dtype=np.int64)
        out[name]=dict(frames=len(kept),fps=len(kept)/wall_s if wall_s else None,
            missing=int(sum(max(0,b-a-1) for a,b in zip(seqs,seqs[1:]))),
            not_increasing=int(sum(b<=a for a,b in zip(seqs,seqs[1:]))),
            interval_ms=percentiles(np.diff(device)/1e6) if len(kept)>1 else None,
            host_to_dequeue_ms=percentiles([(r[3]-r[2])/1e6 for r in kept]),
            bytes_per_frame=int(np.median([r[4] for r in kept])) if kept else None)
    return out

def pair_skew(rows,left,right,settle_ns):
    """Left/right device-time difference for frames sharing a sequence number."""
    r={x[0]:x[1] for x in rows.get(right,[]) if x[3]>=settle_ns}
    skew=[abs(x[1]-r[x[0]])/1e6 for x in rows.get(left,[]) if x[3]>=settle_ns and x[0] in r]
    return percentiles(skew)

def run(config,seconds,settle):
    import depthai as dai
    rows={n:[] for n in stream_names(config)};decode={n:[] for n in rows}
    with dai.Device() as dev:
        dev.setIrLaserDotProjectorIntensity(0);dev.setIrFloodLightIntensity(0)
        usb=str(dev.getUsbSpeed()).split('.')[-1]
        with dai.Pipeline(dev) as pipeline:
            queues={}
            cam=pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
            queues['rgb']=cam.requestOutput((1280,720),type=dai.ImgFrame.Type.BGR888i,fps=15,enableUndistortion=False).createOutputQueue(maxSize=2,blocking=False)
            for side,socket in (('left',dai.CameraBoardSocket.CAM_B),('right',dai.CameraBoardSocket.CAM_C)):
                cam=pipeline.create(dai.node.Camera).build(socket)
                for w,h in CONFIGS[config]:
                    queues[f'{side}_{w}x{h}']=cam.requestOutput((w,h),type=dai.ImgFrame.Type.GRAY8,fps=15,enableUndistortion=False).createOutputQueue(maxSize=2,blocking=False)
            pipeline.start()
            start=time.monotonic_ns();settle_ns=start+int(settle*1e9);end_ns=settle_ns+int(seconds*1e9)
            marks=None
            while pipeline.isRunning():
                now=time.monotonic_ns()
                if marks is None and now>=settle_ns:
                    marks=(resource.getrusage(resource.RUSAGE_SELF),cpu_ticks(),temperature(),now,thread_ticks())
                if now>=end_ns:break
                got=False
                for name,q in queues.items():
                    f=q.tryGet()
                    if f is None:continue
                    got=True;dequeued=time.monotonic_ns()
                    rows[name].append((f.getSequenceNum(),int(f.getTimestampDevice().total_seconds()*1e9),
                        int(f.getTimestamp().total_seconds()*1e9),dequeued,len(f.getData())))
                    # One frame in fifteen is converted to an array, to price that step
                    # without making it part of the capture cost.
                    if len(rows[name])%15==0:
                        t=time.perf_counter_ns();image=f.getCvFrame();decode[name].append((time.perf_counter_ns()-t)/1e6)
                        if name!='rgb' and image.shape[:2]!=tuple(int(x) for x in name.split('_')[1].split('x'))[::-1]:
                            raise RuntimeError(f'{name} delivered {image.shape}')
                if not got:time.sleep(.001)
            if marks is None:raise RuntimeError('pipeline stopped before the settle period ended')
            usage=resource.getrusage(resource.RUSAGE_SELF);ticks=cpu_ticks();stop=time.monotonic_ns();threads=thread_ticks()
    wall=(stop-marks[3])/1e9;total=ticks[0]-marks[1][0];idle=ticks[1]-marks[1][1]
    result=dict(config=config,usb=usb,seconds=wall,settle_seconds=settle,streams=summarise(rows,settle_ns,wall),
        left_right_skew_ms={f'{w}x{h}':pair_skew(rows,f'left_{w}x{h}',f'right_{w}x{h}',settle_ns) for w,h in CONFIGS[config]},
        to_array_ms={n:percentiles(v) for n,v in decode.items()},
        process_cpu_cores=((usage.ru_utime+usage.ru_stime)-(marks[0].ru_utime+marks[0].ru_stime))/wall,
        thread_cores=thread_cores(marks[4],threads,wall,os.sysconf('SC_CLK_TCK')),
        cpu_mhz_at_end=cpu_mhz(),
        system_busy_fraction=1-idle/total if total else None,
        temperature_c=dict(start=marks[2],end=temperature()),throttled=throttled())
    if config=='both':
        # Same sensor, same exposure: the two sizes should carry the same device time.
        result['small_full_skew_ms']={side:pair_skew(rows,f'{side}_640x400',f'{side}_1280x800',settle_ns) for side in ('left','right')}
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--configs',default='small,full,both,small');p.add_argument('--seconds',type=float,default=40)
    p.add_argument('--settle',type=float,default=5);p.add_argument('--max-start-c',type=float,default=72)
    a=p.parse_args();names=a.configs.split(',')
    if any(n not in CONFIGS for n in names) or not 1<=a.seconds<=120:raise SystemExit('bad arguments')
    import depthai as dai
    results=[]
    for name in names:
        if temperature()>=a.max_start_c:results.append(dict(config=name,skipped=f'temperature {temperature()} C at start'));continue
        try:results.append(run(name,a.seconds,a.settle))
        except Exception as exc:results.append(dict(config=name,error=type(exc).__name__+': '+str(exc)))
        a.out.write_text(json.dumps(dict(machine=platform.machine(),depthai=dai.__version__,numpy=np.__version__,results=results),indent=2))
        time.sleep(3)
    a.out.write_text(json.dumps(dict(machine=platform.machine(),depthai=dai.__version__,numpy=np.__version__,results=results),indent=2))
    print(json.dumps(results))
if __name__=='__main__':main()
