"""Bounded, explicit SITL-only FC-bridge rehearsal. Default: print plan; send nothing.

Exercises the production Bridge with scripted camera-command records, NOT live perception.
Only this runner's separate MAVLink command tool arms/changes mode. The bridge cannot.
Read docs/FC_SITL_REHEARSAL.md and obtain peer review before --execute.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/garuda-follow'))
from fc_bridge import Bridge

LIMITS = dict(forward_m_s=.2, yaw_rad_s=.15, motion_s=2., max_radius_m=3.,
              max_altitude_m=5., telemetry_age_s=1., zero_deadline_s=.45,
              fc_loss_deadline_s=.35, receive_only_s=15., handover_settle_s=2.,
              handover_vz_m_s=.10, handover_alt_tolerance_m=.20, altitude_band_m=.20, total_s=180.)
PLAN = ['verify grounded/no owner; save and stop exact PC controller',
        'receive-only loopback 127.0.0.1:14543, PX4 system 4; assert zero packets sent',
        'zero stream disarmed; verify decoded rate/frame/mask/destination',
        'airborne stage only: auto takeoff then offboard; bounded yaw and forward',
        'faults: perception -> zeros; loop stall/ATTITUDE loss -> no stream; mode exit',
        'land/disarm; close socket; restore controller/source; immutable summary']
PARAMETERS = ('COM_OF_LOSS_T', 'COM_OBL_RC_ACT', 'NAV_RCL_ACT', 'COM_RC_IN_MODE',
              'RTL_RETURN_ALT', 'MIS_TAKEOFF_ALT')


def bridge_hash():
    h = hashlib.sha256()
    for name in ('fc_bridge.py', 'fc_gate.py', 'mavlink_min.py', 'live_onboard.py'):
        h.update(name.encode()); h.update((ROOT / 'services/garuda-follow' / name).read_bytes())
    return h.hexdigest()


def snapshot(seq, generation, request, forward=0., yaw=0., valid=True):
    """Explicit synthetic fixture, never passed off as a camera observation."""
    return dict(age_ms=0, transmit=dict(instance='scripted-sitl', generation=generation, request=request),
        stats=dict(instance='scripted-sitl', capabilities=dict(mount='vehicle', camera='cm3', distance_source='mono'),
                   identity=dict(confirmed=valid, state='tracking', target=7, frame_seq=seq,
                                 frame_epoch='scripted', instance='scripted-sitl')),
        frame=dict(seq=seq, epoch='scripted'), lock=dict(id=7, coasting=False),
        tracks=[dict(id=7, label='person', conf=.9, coasting=False)],
        onboard=dict(valid=True, transmit=False, frame_seq=seq, frame_epoch='scripted', instance='scripted-sitl',
            target_id=7, forward_vel=forward, yaw_rate=yaw, vertical_vel=0., range_source='box_height', range_m=None))


def ground(s):
    return s.get('telemetry_fresh') is True and s.get('landed') is True and s.get('armed') is False


def api(path='/api/state', body=None):
    req = urllib.request.Request('http://127.0.0.1:8099' + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'X-Garuda-Session': 'astra-fc-rehearsal'})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.load(r)


def decode_setpoint(m):
    if m.get_type() != 'SET_POSITION_TARGET_LOCAL_NED':
        return None
    if (m.target_system, m.target_component, m.coordinate_frame, m.type_mask) != (4, 1, 8, 1479):
        raise RuntimeError('Unexpected setpoint destination/frame/mask')
    if not all(math.isfinite(v) for v in (m.vx, m.vy, m.vz, m.yaw_rate)):
        raise RuntimeError('Nonfinite setpoint')
    if abs(m.vx) > .201 or m.vy != 0 or m.vz != 0 or abs(m.yaw_rate) > .151:
        raise RuntimeError('Setpoint exceeds preset rehearsal bounds')
    return dict(forward=m.vx, yaw=m.yaw_rate)


def zero_latency(points, started, deadline):
    """Score actual outgoing zero packets; empty evidence cannot pass."""
    zeros=[at-started for at,p in points if at>=started and abs(p['forward'])<1e-6 and abs(p['yaw'])<1e-6]
    if not zeros or not 0 <= min(zeros) <= deadline:
        raise RuntimeError('Zero-command deadline not evidenced')
    return min(zeros)


def decode_parameter(value):
    """PX4 uses bytewise MAVLink encoding for INT32 parameters."""
    if value['param_type']==6:
        import struct
        return struct.unpack('<i',struct.pack('<f',value['param_value']))[0]
    if value['param_type']==9: return float(value['param_value'])
    raise RuntimeError('Unsupported parameter wire type')


def altitude_in_band(z, reference):
    return math.isfinite(z) and abs(z-reference)<=LIMITS['altitude_band_m']


class Wire:
    """One socket; audit outgoing packets and collect scoring-only telemetry."""
    def __init__(self, log):
        from pymavlink.dialects.v20 import common as mav
        self.mav = mav
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 14543)); self.sock.setblocking(False)
        self.parser = mav.MAVLink(None); self.decoder = mav.MAVLink(None)
        self.tool = mav.MAVLink(None, srcSystem=250, srcComponent=190)
        self.log = log; self.messages = {}; self.address = None
        self.parameters = {}
        self.packets = []; self.setpoints = []; self.suppress_heartbeat = False
        self.suppress_attitude=False; self.attitude_progress=[]

    def recvfrom(self, n):
        data, address = self.sock.recvfrom(n)
        if address[0] != '127.0.0.1':
            return b'', address
        parsed=self.parser.parse_buffer(data) or []
        for m in parsed:
            if m.get_srcSystem() != 4 or m.get_srcComponent() != 1:
                continue
            if self.address is None and m.get_type() == 'HEARTBEAT':
                self.address = address
            if address != self.address:
                continue
            if m.get_type()=='ATTITUDE':
                if not self.attitude_progress or m.time_boot_ms>self.attitude_progress[-1][1]:
                    self.attitude_progress.append((time.monotonic(),m.time_boot_ms))
            self.messages[m.get_type()] = (time.monotonic(), m.to_dict())
            if m.get_type() == 'PARAM_VALUE':
                self.parameters[m.param_id] = m.to_dict()  # preserve wire value AND MAV_PARAM_TYPE
            self.log('telemetry', message=m.to_dict())
        # Fault injection hides all traffic from Bridge, not from the scoring observer.
        if self.suppress_heartbeat: return b'',address
        if self.suppress_attitude:
            return b''.join(bytes(m.get_msgbuf()) for m in parsed if m.get_type()!='ATTITUDE'),address
        return data,address

    def sendto(self, packet, address):
        if address != self.address or address is None or address[0] != '127.0.0.1':
            raise RuntimeError('Unpinned destination')
        for m in self.decoder.parse_buffer(packet) or []:
            point = decode_setpoint(m)
            if point is not None:
                self.setpoints.append((time.monotonic(), point))
            elif m.get_type() != 'HEARTBEAT':
                raise RuntimeError('Bridge tried to send an unexpected message')
            self.log('bridge_tx', message=m.to_dict())
        result = self.sock.sendto(packet, address)
        self.packets.append(time.monotonic())
        return result

    def command(self, command, *params):
        if self.address is None:
            raise RuntimeError('No pinned SITL peer')
        allowed = {self.mav.MAV_CMD_COMPONENT_ARM_DISARM, self.mav.MAV_CMD_DO_SET_MODE,
                   self.mav.MAV_CMD_NAV_LAND, self.mav.MAV_CMD_SET_MESSAGE_INTERVAL}
        if command not in allowed:
            raise RuntimeError('Command not allowlisted')
        msg = self.tool.command_long_encode(4, 1, command, 0, *(list(params) + [0.] * (7-len(params))))
        packet = msg.pack(self.tool); self.tool.seq = (self.tool.seq + 1) & 255
        self.log('tool_tx', message=msg.to_dict())
        self.sock.sendto(packet, self.address)

    def telemetry(self, name):
        at, msg = self.messages.get(name, (-math.inf, {}))
        if time.monotonic() - at > LIMITS['telemetry_age_s']:
            raise RuntimeError('Stale/missing ' + name)
        return msg

    def request_parameters(self):
        if self.address is None: raise RuntimeError('No pinned SITL peer')
        for name in PARAMETERS:
            msg=self.tool.param_request_read_encode(4,1,name.encode(),-1)
            packet=msg.pack(self.tool); self.tool.seq=(self.tool.seq+1)&255
            self.log('parameter_read',name=name)
            self.sock.sendto(packet,self.address)


class Trial:
    def __init__(self, wire, log):
        self.wire, self.log = wire, log
        self.bridge = Bridge(wire, 4, '127.0.0.1', receive_only=True)
        self.seq = self.gen = 0; self.request = 'off'; self.forward = self.yaw = 0.
        self.fault = None; self.fault_at = 0.; self.origin = None; self.envelope = False
        self.height_reference=None
        self.height_context='normal commanded flight'
        self.start = time.monotonic(); self.last = None; self.rows = []

    def tick(self, stage, cleanup=False):
        if not cleanup and time.monotonic()-self.start > LIMITS['total_s']:
            raise RuntimeError('Overall timeout')
        if self.fault != 'freeze': self.seq += 1
        s = snapshot(self.seq, self.gen, self.request, self.forward, self.yaw, self.fault != 'identity')
        age = (time.monotonic()-self.fault_at)*1000 if self.fault == 'freeze' else 0.
        self.last = self.bridge.tick(None if self.fault == 'missing' else s, age, age)
        self.log('tick', stage=stage, report=self.last)
        self.rows.append((time.monotonic(), stage, dict(self.last)))
        if self.envelope and not cleanup:
            p = self.wire.telemetry('LOCAL_POSITION_NED')
            if math.hypot(p['x']-self.origin[0], p['y']-self.origin[1]) > LIMITS['max_radius_m'] or not -LIMITS['max_altitude_m'] < p['z']-self.origin[2] < .5:
                raise RuntimeError('Flight envelope exceeded')
            if self.height_reference is not None and not altitude_in_band(p['z'],self.height_reference):
                self.log('altitude_band_failed',z=p['z'],reference=self.height_reference,band=LIMITS['altitude_band_m'],
                         context=self.height_context,heartbeat=self.wire.messages.get('HEARTBEAT'))
                raise RuntimeError('Altitude band exceeded: '+self.height_context)
        return self.last

    def period(self, stage, seconds, cleanup=False):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            self.tick(stage, cleanup); time.sleep(.05)

    def wait(self, stage, predicate, timeout, cleanup=False):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            self.tick(stage, cleanup)
            try:
                if predicate(): return
            except (KeyError, RuntimeError) as exc:
                self.log('wait_not_ready',stage=stage,reason=str(exc))
            time.sleep(.05)
        raise RuntimeError(stage+' timeout')

    def off(self):
        self.gen += 1; self.request = 'off'; self.forward = self.yaw = 0.

    def grant(self, forward=0., yaw=.15):
        self.open_session()
        self.gen += 1; self.request = 'fc'; self.forward = forward; self.yaw = yaw
        self.period('new_grant', .25)
        if self.last['state'] != 'sending': raise RuntimeError('Explicit grant refused: '+self.last['reason'])

    def open_session(self):
        self.off(); self.period('off_before_new_session',.2)
        self.gen+=1; self.request='fc_hold'
        self.period('explicit_hold_session',.25)
        if not self.last['stream'] or self.last['state']=='sending':
            raise RuntimeError('Explicit zero-stream session refused: '+self.last['reason'])

    def silent(self, stage, seconds=1.):
        before=len(self.wire.packets)
        self.period(stage,seconds)
        if len(self.wire.packets)!=before or self.last['stream']:
            raise RuntimeError(stage+': session restarted without a new hold request')

    def wait_settled(self, target_z):
        since=[None]
        def settled():
            try: p=self.wire.telemetry('LOCAL_POSITION_NED')
            except (KeyError,RuntimeError):
                since[0]=None
                raise
            now=time.monotonic()
            if abs(p['z']-target_z)>LIMITS['handover_alt_tolerance_m'] or abs(p['vz'])>LIMITS['handover_vz_m_s']:
                since[0]=None; return False
            if since[0] is None: since[0]=now
            return now-since[0]>=LIMITS['handover_settle_s']
        self.wait('settled_takeoff',settled,35)

    def held(self, stage, seconds=1.):
        start = len(self.wire.setpoints)
        self.period(stage, seconds)
        points = self.wire.setpoints[start:]
        if not points or any(abs(p['forward']) > 1e-6 or abs(p['yaw']) > 1e-6 for _, p in points):
            raise RuntimeError(stage+': zero setpoints not evidenced')
        if self.last['state'] == 'sending': raise RuntimeError(stage+': old grant resumed')

    def airborne(self):
        m = self.wire.mav
        self.wire.request_parameters()
        self.wait('read_parameters',lambda:all(k in self.wire.parameters for k in PARAMETERS),5)
        self.log('sitl_parameters',values=self.wire.parameters)
        self.origin = tuple(self.wire.telemetry('LOCAL_POSITION_NED')[k] for k in ('x','y','z'))
        self.envelope = True
        takeoff_alt=decode_parameter(self.wire.parameters['MIS_TAKEOFF_ALT'])
        if not .8<=takeoff_alt<=LIMITS['max_altitude_m']-.5: raise RuntimeError('Configured takeoff altitude outside rehearsal envelope')
        self.wire.command(m.MAV_CMD_DO_SET_MODE, 1, 4, 2)  # PX4 AUTO TAKEOFF, simulator only
        self.wire.command(m.MAV_CMD_COMPONENT_ARM_DISARM, 1)
        self.wait_settled(self.origin[2]-takeoff_alt)
        self.height_reference=self.wire.telemetry('LOCAL_POSITION_NED')['z']
        self.log('height_handover',reference=self.height_reference,band=LIMITS['altitude_band_m'])
        self.wire.command(m.MAV_CMD_DO_SET_MODE, 1, 6)
        self.wait('offboard', lambda:bool(self.last.get('fc', {}).get('offboard')), 5)
        self.period('settle', 2)
        yaw0 = self.wire.telemetry('ATTITUDE')['yaw']
        self.grant(yaw=LIMITS['yaw_rad_s']); self.period('yaw', LIMITS['motion_s'])
        dy = self.wire.telemetry('ATTITUDE')['yaw']-yaw0
        dy = math.atan2(math.sin(dy), math.cos(dy))
        if not .08 < dy < .7: raise RuntimeError('Yaw direction/magnitude check failed: '+str(dy))
        self.off(); self.held('yaw_off'); self.period('settle', 1)
        p0 = self.wire.telemetry('LOCAL_POSITION_NED'); heading = self.wire.telemetry('ATTITUDE')['yaw']
        self.grant(forward=LIMITS['forward_m_s'], yaw=0); self.period('forward', LIMITS['motion_s'])
        p1 = self.wire.telemetry('LOCAL_POSITION_NED')
        dx, dy = p1['x']-p0['x'], p1['y']-p0['y']
        along = dx*math.cos(heading)+dy*math.sin(heading)
        across = -dx*math.sin(heading)+dy*math.cos(heading)
        if not .08 < along < .8 or abs(across) > .3: raise RuntimeError('Body forward check failed')
        self.off(); self.held('forward_off')
        for fault in ('identity', 'missing', 'freeze'):
            self.grant(); self.fault=fault; self.fault_at=time.monotonic()
            self.log('fault_injected',fault=fault,at=self.fault_at)
            self.period(fault, LIMITS['zero_deadline_s'])
            self.held(fault+'_zero', .3)
            latency=zero_latency(self.wire.setpoints,self.fault_at,LIMITS['zero_deadline_s'])
            self.log('fault_score',fault=fault,zero_latency_s=latency)
            self.fault=None; self.held(fault+'_no_resume')
        self.grant(); self.height_context='intentional stream-loss/recovery window (not steady height hold)'
        gap_from=self.wire.setpoints[-1][0]
        self.log('fault_injected',fault='loop_stall',last_setpoint_at=gap_from); time.sleep(.3)
        self.silent('loop_stall_no_resume',.25)
        # New explicit stream authorization; do not rely on PX4 automatically returning to offboard.
        self.open_session(); self.wire.command(m.MAV_CMD_DO_SET_MODE,1,6)
        self.wait('offboard_after_explicit_session',lambda:self.last.get('fc') is not None and self.last['fc']['offboard'],5)
        resumed=next(at for at,_ in self.wire.setpoints if at>gap_from)
        self.log('stream_gap_recovery',gap_s=resumed-gap_from,heartbeat=self.wire.messages.get('HEARTBEAT'))
        self.period('settle_after_explicit_session',2)
        self.height_context='normal commanded flight'
        self.grant(); self.wire.command(m.MAV_CMD_DO_SET_MODE, 1, 4, 3)  # AUTO/LOITER; no RC required
        self.wait('mode_exit', lambda:self.last.get('fc') is not None and not self.last['fc']['offboard'], 3)
        self.wire.command(m.MAV_CMD_DO_SET_MODE, 1, 6)
        self.wait('mode_return', lambda:self.last.get('fc') is not None and self.last['fc']['offboard'], 3)
        self.held('mode_no_resume')
        # Last case: hide only fast attitude; heartbeat remains visible. No automatic stream return.
        self.grant(); self.wire.suppress_attitude=True
        self.log('fault_injected',fault='attitude_loss'); injected=time.monotonic()
        self.period('attitude_loss',LIMITS['fc_loss_deadline_s'])
        if self.last['stream']: raise RuntimeError('Fast telemetry loss deadline missed')
        self.log('fault_score',fault='attitude_loss',checked_stopped_after_s=time.monotonic()-injected)
        self.silent('attitude_no_tx',.3)
        # Loss action is scored separately; original overall envelope still applies.
        self.height_reference=None
        self.period('observe_px4_loss_action', 3)
        self.log('telemetry_loss_mode',heartbeat=self.wire.telemetry('HEARTBEAT'))
        self.wire.suppress_attitude=False
        self.wait('attitude_return', lambda:self.last.get('fc') is not None, 3)
        self.silent('attitude_no_resume')

    def cleanup_land(self, airborne, timeout=45.):
        """Keep receiving during cleanup; missing telemetry must not suppress LAND."""
        self.off(); self.fault=None; self.wire.suppress_heartbeat=False; self.wire.suppress_attitude=False
        deadline=time.monotonic()+timeout; next_land=-math.inf
        while time.monotonic()<deadline:
            try: self.tick('cleanup_land',cleanup=True)
            except Exception as exc: self.log('cleanup_tick_error',reason=str(exc))
            try:
                hb=self.wire.telemetry('HEARTBEAT')
                disarmed=not (hb['base_mode'] & 128)
                landed=(self.wire.telemetry('EXTENDED_SYS_STATE')['landed_state']==1) if airborne else True
                if disarmed and landed: return True
            except (KeyError,RuntimeError): pass
            # Armed or unknown: send/retry LAND to the already-pinned simulator, never force disarm.
            if self.wire.address is not None and time.monotonic()>=next_land:
                try: self.wire.command(self.wire.mav.MAV_CMD_NAV_LAND)
                except Exception as exc: self.log('land_send_error',reason=str(exc))
                next_land=time.monotonic()+2.
            ack=self.wire.messages.get('COMMAND_ACK')
            if ack: self.log('cleanup_ack',at=ack[0],message=ack[1])
            time.sleep(.05)
        raise RuntimeError('LAND/ground not confirmed before cleanup timeout')


def process_identity(pid):
    try: return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
    except (FileNotFoundError,ProcessLookupError): return None


def update_process_record(cwd, prior_pid, new_pid):
    path=Path(cwd)/'.runtime/processes/dashboard.json'
    old=json.loads(path.read_text()) if path.exists() else {}
    if old and old['pid']!=prior_pid and process_identity(old['pid'])==old.get('identity'):
        raise RuntimeError('Another live dashboard owns the process record')
    identity=process_identity(new_pid)
    if identity is None: raise RuntimeError('Restored dashboard exited')
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(dict(pid=new_pid,identity=identity))); tmp.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true'); p.add_argument('--airborne', action='store_true')
    p.add_argument('--controller-pid', type=int); p.add_argument('--output', type=Path)
    p.add_argument('--coordination', type=Path); p.add_argument('--reviewed-bridge-sha256')
    args=p.parse_args()
    if not args.execute:
        print(json.dumps(dict(plan=PLAN, limits=LIMITS, bridge_sha256=bridge_hash(), sends=False), indent=2)); return
    if os.environ.get('GARUDA_SIM')!='1' or os.environ.get('GARUDA_FC_BRIDGE')=='1':
        raise SystemExit('Only GARUDA_SIM=1; real-FC environment forbidden')
    if not args.output or not args.controller_pid or not args.coordination or args.reviewed_bridge_sha256!=bridge_hash():
        raise SystemExit('Requires immutable output, exact controller PID, coordination dir and reviewed bridge hash')
    before=api()
    if not ground(before) or before['control']['owner'] is not None:
        raise SystemExit('Must be grounded, disarmed, fresh and no operator lease')
    proc=Path('/proc')/str(args.controller_pid)
    argv=[s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
    if 'services/garuda-follow/sitl_demo.py' not in argv: raise SystemExit('Wrong controller PID')
    env=dict(s.decode().split('=',1) for s in (proc/'environ').read_bytes().split(b'\0') if s)
    if env.get('GARUDA_SIM')!='1' or env.get('ROS_DOMAIN_ID')!='42': raise SystemExit('Wrong controller isolation')
    exe=os.readlink(proc/'exe'); cwd=os.readlink(proc/'cwd')
    args.output.mkdir(parents=True, exist_ok=False)
    lock=args.coordination/'pi.lock'; lock.mkdir()  # coordinates with peer hardware/simulator work
    (lock/'holder.json').write_text(json.dumps(dict(holder='astra', purpose='FC SITL rehearsal', output=str(args.output))))
    log_file=(args.output/'events.jsonl').open('x')
    def log(event, **values):
        log_file.write(json.dumps(dict(event=event, monotonic=time.monotonic(), **values))+'\n'); log_file.flush()
    (args.output/'manifest.json').write_text(json.dumps(dict(plan=PLAN, limits=LIMITS, before=before,
        bridge_sha256=bridge_hash(), argv=argv, cwd=cwd, airborne=args.airborne, input='SCRIPTED; no Pi camera'), indent=2))
    errors=[]; wire=trial=None; stopped=False; safe=False; restored=False
    try:
        # A second known publisher is a blocker, not something this runner kills.
        others=[]
        for item in Path('/proc').iterdir():
            if item.name.isdigit() and int(item.name)!=args.controller_pid:
                try: cmd=(item/'cmdline').read_bytes().split(b'\0')
                except OSError: continue
                if any(v.endswith(b'/sitl_demo.py') or v.endswith(b'/fc_bridge.py') for v in cmd): others.append(item.name)
        if others: raise RuntimeError('Other known publishers: '+str(others))
        os.kill(args.controller_pid, signal.SIGTERM)
        for _ in range(50):
            if not proc.exists(): break
            time.sleep(.1)
        if proc.exists(): raise RuntimeError('Controller did not stop')
        stopped=True; wire=Wire(log); trial=Trial(wire,log)
        trial.period('receive_only', LIMITS['receive_only_s'])
        if wire.packets or trial.last.get('fc') is None or trial.last['fc']['armed']:
            raise RuntimeError('Receive-only preflight failed')
        trial.bridge.receive_only=False
        progress=wire.attitude_progress
        gaps=[b[0]-a[0] for a,b in zip(progress,progress[1:])]
        log('receive_only_cadence',advancing_attitudes=len(progress),max_gap_s=max(gaps) if gaps else None)
        if len(progress)<100 or not gaps or max(gaps)>.3:
            raise RuntimeError('Fast telemetry cadence has not supported the proposed300ms bound')
        trial.period('no_session_no_tx',.5)
        if wire.packets: raise RuntimeError('Packets emitted before explicit stream session')
        trial.open_session()
        trial.period('zero_disarmed', 2)
        points=wire.setpoints
        if len(points)<20 or any(p['forward'] or p['yaw'] for _,p in points): raise RuntimeError('Zero stream failed')
        intervals=[b[0]-a[0] for a,b in zip(points,points[1:])]
        if not intervals or max(intervals)>.15 or min(intervals)<.025: raise RuntimeError('Unexpected stream cadence')
        if args.airborne:
            for mid in (32,30,245): wire.command(wire.mav.MAV_CMD_SET_MESSAGE_INTERVAL, mid, 100000)
            trial.period('telemetry', 2); trial.airborne()
    except BaseException as exc:
        errors.append(type(exc).__name__+': '+str(exc))
    finally:
        if trial:
            try:
                safe=trial.cleanup_land(args.airborne)
            except Exception as exc: errors.append('Landing unconfirmed: '+str(exc))
            wire.sock.close(); log('bridge_socket_closed')
        if stopped and safe:
            try:
                out=(args.output/'restored-controller.log').open('ab')
                child=subprocess.Popen([exe,*argv[1:]],env=env,cwd=cwd,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
                update_process_record(cwd,args.controller_pid,child.pid)
                for _ in range(50):
                    try:
                        if ground(api()): break
                    except Exception: pass
                    time.sleep(.2)
                if not ground(api()): raise RuntimeError('Restored controller not grounded/fresh')
                api('/api/command',dict(command='source',kind=before['source_kind']))
                time.sleep(.2)
                if api()['source_kind']!=before['source_kind']: raise RuntimeError('Source not restored')
                api('/api/command',dict(command='settings',**{k:before[k] for k in ('gap','speed','limit','direction')}))
                time.sleep(.2)
                restored_state=api()
                if any(restored_state[k]!=before[k] for k in ('gap','speed','limit','direction')):
                    raise RuntimeError('Settings not restored')
                restored=True; log('restored',pid=child.pid,source=before['source_kind'])
            except Exception as exc: errors.append('Restoration failed: '+str(exc))
        elif not stopped:
            restored=True
        else: errors.append('Controller kept stopped: ground state unconfirmed; inspect SITL')
        (args.output/'summary.json').write_text(json.dumps(dict(errors=errors,ground_confirmed=safe,
            restored=restored,passed=not errors and safe and restored,airborne=args.airborne),indent=2))
        log_file.close()
        if restored:
            (lock/'holder.json').unlink(); lock.rmdir()
        else: print('Coordination lock retained for recovery:',lock)
    if errors: raise SystemExit('; '.join(errors))


if __name__=='__main__': main()
