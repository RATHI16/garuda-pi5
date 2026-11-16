"""Real fc_bridge.main + Pi HTTP/status, DISARMED zero-stream rehearsal only.

No arm/mode commands; relay rejects every nonzero setpoint. Default prints a plan.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from fc_sitl_rehearsal import ROOT, api, ground, bridge_hash, update_process_record

PI='http://10.42.0.128:8098'
FC=('127.0.0.1',14583)
BRIDGE=('127.0.0.1',14643)
PLAN=['fresh grounded/no-owner; acquire lock; stop exact PC controller',
      'audited loopback relay; actual bridge process receive-only against real Pi',
      'hold request refused receive-only; process exit expires listener/request',
      'sending process remains silent until fresh hold; verify zeros/status through real HTTP',
      'follow off preserves zero stream; process stall ends it; new hold explicitly restarts',
      'stop bridge; verify listener expiry; restore Pi fixed/off and PC/source/settings']


def pi(path='/api/state',post=False):
    req=urllib.request.Request(PI+path,data=b'' if post else None)
    with urllib.request.urlopen(req,timeout=2) as response:return json.load(response)


def audit_outgoing(messages, heartbeat, now):
    """Fail before forwarding: only disarmed SITL, exact sources and zero commands."""
    if heartbeat is None or now-heartbeat[0]>1.5 or heartbeat[1]['base_mode']&128:
        raise RuntimeError('No fresh disarmed simulator heartbeat')
    if not messages:raise RuntimeError('Undecodable bridge packet')
    points=0
    for m in messages:
        if (m.get_srcSystem(),m.get_srcComponent())!=(4,191):raise RuntimeError('Unexpected bridge sender')
        if m.get_type()=='HEARTBEAT':continue
        if m.get_type()!='SET_POSITION_TARGET_LOCAL_NED':raise RuntimeError('Command message forbidden in ground relay')
        if (m.target_system,m.target_component,m.coordinate_frame,m.type_mask)!=(4,1,8,1479):raise RuntimeError('Wrong setpoint target/frame/mask')
        if any(v!=0 for v in (m.vx,m.vy,m.vz,m.yaw_rate)):raise RuntimeError('Nonzero command forbidden in ground relay')
        points+=1
    return points


class Relay:
    def __init__(self,log):
        from pymavlink.dialects.v20 import common as mav
        self.log=log;self.stop=threading.Event();self.error=None;self.hb=None
        self.forward_enabled=threading.Event()
        self.tx=0;self.points=0;self.last_tx=None;self.rx=0
        self.fc_parser=mav.MAVLink(None);self.bridge_parser=mav.MAVLink(None)
        self.up=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.down=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            self.up.bind(('127.0.0.1',14543));self.down.bind(('127.0.0.1',14644))
            self.up.setblocking(False);self.down.setblocking(False)
        except BaseException:
            self.up.close();self.down.close();raise
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def run(self):
        try:
            while not self.stop.is_set():
                ready,_,_=select.select([self.up,self.down],[],[],.02)
                for sock in ready:
                    data,address=sock.recvfrom(65535);now=time.monotonic()
                    if sock is self.up:
                        if address!=FC:raise RuntimeError('Unexpected simulator UDP source')
                        messages=self.fc_parser.parse_buffer(data) or []
                        for m in messages:
                            if (m.get_srcSystem(),m.get_srcComponent())==(4,1) and m.get_type()=='HEARTBEAT':
                                self.hb=(now,m.to_dict())
                                self.log('fc_heartbeat',message=m.to_dict())
                                if m.base_mode&128:raise RuntimeError('Simulator armed during ground-only trial')
                            if m.get_type()=='ATTITUDE' and (m.get_srcSystem(),m.get_srcComponent())==(4,1):
                                self.log('fc_attitude',boot_ms=m.time_boot_ms)
                        self.rx+=1
                        if self.forward_enabled.is_set():self.down.sendto(data,BRIDGE)
                    else:
                        if address!=BRIDGE:raise RuntimeError('Unexpected bridge UDP source')
                        messages=self.bridge_parser.parse_buffer(data) or []
                        count=audit_outgoing(messages,self.hb,now)
                        self.up.sendto(data,FC)
                        self.tx+=1;self.points+=count;self.last_tx=now
                        self.log('forwarded',hex=data.hex(),messages=[m.to_dict() for m in messages])
        except BaseException as exc:
            self.error=str(exc);self.log('relay_error',reason=self.error);self.stop.set()

    def check(self):
        if self.error:raise RuntimeError(self.error)
        if not self.thread.is_alive():raise RuntimeError('Relay stopped')

    def close(self):
        self.stop.set();self.thread.join(timeout=1);self.up.close();self.down.close()


def contract(s):
    return dict(age_ms=s.get('age_ms'),instance=s.get('stats',{}).get('instance'),
        frame=s.get('frame'),capabilities=s.get('stats',{}).get('capabilities'),transmit=s.get('transmit'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--controller-pid',type=int)
    p.add_argument('--output',type=Path);p.add_argument('--coordination',type=Path)
    p.add_argument('--reviewed-bridge-sha256');args=p.parse_args()
    if not args.execute:
        print(json.dumps(dict(plan=PLAN,sends=False,bridge_sha256=bridge_hash()),indent=2));return
    if os.environ.get('GARUDA_SIM')!='1' or os.environ.get('GARUDA_FC_BRIDGE')=='1':raise SystemExit('Simulator environment only')
    if not all((args.controller_pid,args.output,args.coordination)) or args.reviewed_bridge_sha256!=bridge_hash():raise SystemExit('Missing exact PID/output/coordination/reviewed hash')
    before=api()
    if not ground(before) or before['control']['owner'] is not None:raise SystemExit('Grounded/disarmed/idle required')
    proc=Path('/proc')/str(args.controller_pid)
    argv=[v.decode() for v in (proc/'cmdline').read_bytes().split(b'\0') if v]
    if 'services/garuda-follow/sitl_demo.py' not in argv:raise SystemExit('Wrong controller PID')
    env=dict(v.decode().split('=',1) for v in (proc/'environ').read_bytes().split(b'\0') if v)
    if env.get('GARUDA_SIM')!='1' or env.get('ROS_DOMAIN_ID')!='42':raise SystemExit('Wrong controller isolation')
    exe=os.readlink(proc/'exe');cwd=os.readlink(proc/'cwd')
    args.output.mkdir(parents=True,exist_ok=False)
    lock=args.coordination/'pi.lock';lock.mkdir()
    (lock/'holder.json').write_text(json.dumps(dict(holder='astra',purpose='real Pi HTTP bridge ground relay',output=str(args.output))))
    f=(args.output/'events.jsonl').open('x');mutex=threading.Lock()
    def log(event,**values):
        with mutex:f.write(json.dumps(dict(event=event,monotonic=time.monotonic(),**values))+'\n');f.flush()
    errors=[];checks=[];relay=None;child=None;stopped=False;restored=False;pi_changed=False;paused=False
    started=time.monotonic()
    def sample(stage):
        if time.monotonic()-started>100:raise RuntimeError('Overall trial deadline')
        if relay:relay.check()
        if child and child.poll() is not None:raise RuntimeError('Bridge process exited')
        s=pi();log('pi_contract',stage=stage,**contract(s));return s
    def until(stage,predicate,seconds=4):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            s=sample(stage)
            if predicate(s):return s
            time.sleep(.1)
        raise RuntimeError(stage+' timed out')
    def duration(stage,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:sample(stage);time.sleep(.1)
    def listener(s):return s['transmit']['listeners']['fc']
    def ask(target):
        out=pi('/api/transmit?target='+target,True)
        if out.get('ok') is not True:raise RuntimeError('Pi refused request '+target)
        log('operator_request',request=target)
    def launch(receive):
        nonlocal child
        path=args.output/('receive-only.log' if receive else 'sending.log')
        cmd=[sys.executable,str(ROOT/'services/garuda-follow/fc_bridge.py'),'--simulator',
             '--listen','127.0.0.1:14643','--fc-host','127.0.0.1','--fc-sysid','4','--source',PI]
        if receive:cmd.append('--receive-only')
        with path.open('xb') as out:child=subprocess.Popen(cmd,env=dict(os.environ,GARUDA_SIM='1'),stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        log('bridge_started',pid=child.pid,receive_only=receive)
        until('bridge_http_listener_online',lambda s:listener(s)['alive'])
        relay.forward_enabled.set()
    def stop_child():
        nonlocal child,paused
        if child:
            if relay:relay.forward_enabled.clear()
            if paused:child.send_signal(signal.SIGCONT);paused=False
            child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=2)
            log('bridge_stopped',pid=child.pid,returncode=child.returncode);child=None
    try:
        pi_before=pi();log('pi_before',**contract(pi_before))
        caps=pi_before.get('stats',{}).get('capabilities',{})
        if caps.get('mount')!='fixed' or pi_before['transmit']['request']!='off':raise RuntimeError('Requires Pi fixed/off before this trial')
        if caps.get('mount_locked'):raise RuntimeError('Pi mount is locked')
        (args.output/'manifest.json').write_text(json.dumps(dict(plan=PLAN,code_hash=bridge_hash(),controller=before,
            pi=contract(pi_before),pid=args.controller_pid,cwd=cwd,argv=argv,scope='real HTTP/status; DISARMED zero stream only'),indent=2))
        for item in Path('/proc').iterdir():
            if item.name.isdigit() and int(item.name)!=args.controller_pid:
                try:cmd=(item/'cmdline').read_bytes().split(b'\0')
                except OSError:continue
                if any(v.endswith(b'/sitl_demo.py') or v.endswith(b'/fc_bridge.py') for v in cmd):raise RuntimeError('Other known publisher '+item.name)
        os.kill(args.controller_pid,signal.SIGTERM)
        for _ in range(50):
            if not proc.exists():break
            time.sleep(.1)
        if proc.exists():raise RuntimeError('Controller did not exit')
        stopped=True;relay=Relay(log)
        out=pi('/api/mount?value=vehicle',True);pi_changed=True
        if out.get('ok') is not True:raise RuntimeError('Mount request failed')
        launch(True)
        until('listener_ready',lambda s:listener(s)['alive'])
        duration('receive_only',3);ask('fc_hold')
        until('receive_only_refusal',lambda s:listener(s)['state']=='refused' and listener(s).get('streaming') is False)
        if relay.tx:raise RuntimeError('Receive-only emitted UDP')
        checks.append('receive-only silent and bound refusal')
        stop_child();until('receive_listener_expired',lambda s:not listener(s)['alive'] and s['transmit']['request']=='off')
        launch(False);until('sending_listener_ready',lambda s:listener(s)['alive'])
        duration('no_session',2)
        if relay.tx:raise RuntimeError('Stream started without new hold')
        checks.append('startup remains silent')
        ask('fc_hold');until('hold_stream',lambda s:listener(s).get('streaming') is True and listener(s)['state']=='holding')
        before_points=relay.points;duration('hold_stream',3)
        if relay.points-before_points<20:raise RuntimeError('Insufficient zero stream evidence')
        checks.append('real hold request opens zero stream')
        ask('off');until('off_preserves_stream',lambda s:s['transmit']['request']=='off' and listener(s).get('streaming') is True)
        before_points=relay.points;duration('off_preserves_stream',1)
        if relay.points<=before_points:raise RuntimeError('Off status claimed stream but no packets')
        checks.append('off reports continuing zero stream')
        ask('fc_hold');until('hold_before_stall',lambda s:listener(s)['state']=='holding' and listener(s).get('streaming') is True)
        child.send_signal(signal.SIGSTOP);paused=True;log('process_stall_start')
        time.sleep(.4);count=relay.tx
        child.send_signal(signal.SIGCONT);paused=False;log('process_stall_end')
        until('stall_session_ended',lambda s:listener(s).get('streaming') is False and listener(s)['state']=='refused')
        duration('stall_no_resume',1)
        if relay.tx!=count:raise RuntimeError('Old session streamed after process stall')
        checks.append('process stall ends session, no old request resume')
        ask('off');duration('explicit_off_before_recovery',.3);ask('fc_hold')
        until('new_session',lambda s:listener(s).get('streaming') is True)
        before_points=relay.points;duration('new_session',1)
        if relay.points<=before_points:raise RuntimeError('New session lacked wire evidence')
        checks.append('fresh explicit hold restores zero stream')
        stop_child();time.sleep(.1);count=relay.tx
        until('listener_expired',lambda s:not listener(s)['alive'] and s['transmit']['request']=='off')
        if relay.tx!=count:raise RuntimeError('Packets after process exit')
        checks.append('process exit expires listener and request')
    except BaseException as exc:errors.append(type(exc).__name__+': '+str(exc))
    finally:
        try:stop_child()
        except Exception as exc:errors.append('Bridge stop: '+str(exc))
        pi_restored=not pi_changed
        if pi_changed:
            try:
                ask('off');pi('/api/mount?value=fixed',True);s=pi()
                pi_restored=s['transmit']['request']=='off' and s['stats']['capabilities']['mount']=='fixed'
                if not pi_restored:raise RuntimeError('Pi fixed/off not confirmed')
                log('pi_restored',**contract(s))
            except Exception as exc:errors.append('Pi restore: '+str(exc))
        safe=relay is not None and relay.hb is not None and time.monotonic()-relay.hb[0]<1.5 and not (relay.hb[1]['base_mode']&128)
        if relay:
            relay.close();log('relay_closed',tx=relay.tx,setpoints=relay.points,error=relay.error)
            if relay.error and relay.error not in errors:errors.append('Relay: '+relay.error)
        if stopped and safe:
            try:
                with (args.output/'restored-controller.log').open('ab') as out:
                    restored_child=subprocess.Popen([exe,*argv[1:]],cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
                update_process_record(cwd,args.controller_pid,restored_child.pid)
                for _ in range(50):
                    try:
                        if ground(api()):break
                    except Exception:pass
                    time.sleep(.2)
                if not ground(api()):raise RuntimeError('Controller ground state not confirmed')
                api('/api/command',dict(command='source',kind=before['source_kind']));time.sleep(.2)
                api('/api/command',dict(command='settings',**{k:before[k] for k in ('gap','speed','limit','direction')}));time.sleep(.2)
                s=api()
                if not ground(s) or any(s[k]!=before[k] for k in ('source_kind','gap','speed','limit','direction')):raise RuntimeError('Restored settings mismatch')
                restored=True;log('controller_restored',pid=restored_child.pid,source=s['source_kind'])
            except Exception as exc:errors.append('Controller restore: '+str(exc))
        elif not stopped:restored=True
        if not restored:errors.append('Controller remains stopped; manual recovery required')
        report=dict(passed=not errors and restored and pi_restored,checks=checks,errors=errors,controller_restored=restored,
                    pi_restored=pi_restored,all_commands_zero=True if relay and not relay.error else None,
                    follow_grant='NOT TESTED: hold-only contract stage; no person selected',airborne=False)
        (args.output/'summary.json').write_text(json.dumps(report,indent=2));f.close();print(json.dumps(report,indent=2))
        if restored and pi_restored:(lock/'holder.json').unlink();lock.rmdir()
    if errors:raise SystemExit('; '.join(errors))


if __name__=='__main__':main()
