#!/usr/bin/env python3
"""PC-only ROS 2/PX4 demo, reading the working Pi service without mutating it."""
import argparse
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import urllib.request
import urllib.error
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from sitl_demo_core import DemoControl
from pose_history import PoseHistory
from observation_client import ObservationClient
from delivery_counts import DeliveryCounts
from control_lease import ControlLease
import mono_estimate
import onboard_visual
from bench_turn import BenchTurn, grounded
from sitl_presence import SitlPresence
from live_onboard import LiveGate, SenderStatus
from onboard_source import horizontal as body_horizontal
from onboard_selection import OnboardSelection, current_command, read_with_selection

ROOT=Path(__file__).resolve().parents[2]

def select_command(core, action, now):
    """Selection action shared by the live command handler and offline tests."""
    tid=int(action['id']);snapshot=core.snapshot
    if not any(t['id']==tid and not t.get('coasting') for t in snapshot.get('tracks',[])):
        raise ValueError('Target no longer visible')
    epoch=snapshot.get('frame',{}).get('epoch')
    if action.get('epoch',epoch)!=epoch:raise ValueError('Image source changed; select again')
    if tid==core.target_id and core.epoch==epoch and not core.requires_reselection:return
    core.select(tid)
    core.track(now)


def cors_headers(origin, allowed):
    """Response headers that let one explicitly allowed page origin use this local simulator
    API from the browser. Empty for any other origin: no wildcard, nothing by default."""
    if not origin or origin not in allowed:return {}
    return {'Access-Control-Allow-Origin':origin,'Vary':'Origin','Access-Control-Allow-Methods':'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers':'Content-Type, X-Garuda-Session','Access-Control-Allow-Private-Network':'true','Access-Control-Max-Age':'600'}


def camera_select(base, snapshot, tid, opener=urllib.request.urlopen):
    """Fixed-stereo source: the camera measures depth only for the track it has selected, so a
    selection here is first made on the camera, by exact track ID for the frame the operator
    saw. Raises ValueError with the camera's reason if it does not confirm exactly that."""
    from urllib.parse import urlencode
    frame=snapshot.get('frame',{});instance=snapshot.get('stats',{}).get('instance')
    if not isinstance(instance,str) or not instance or not isinstance(frame.get('epoch'),str):
        raise ValueError('Camera identity unknown; select again')
    query=urlencode({'id':int(tid),'epoch':frame['epoch'],'instance':instance})
    try:
        with opener(urllib.request.Request(base+'/api/select_track?'+query,data=b'',method='POST'),timeout=1) as r:got=json.load(r)
    except urllib.error.HTTPError as e:
        try:reason=json.load(e).get('reason')
        except Exception:reason=None
        raise ValueError('Camera refused the selection: '+str(reason or 'HTTP '+str(e.code))) from None
    except Exception as e:
        raise ValueError('Camera unreachable for selection: '+type(e).__name__) from None
    if not isinstance(got,dict) or got.get('ok') is not True or got.get('id')!=int(tid) or got.get('epoch')!=frame['epoch'] or got.get('instance')!=instance:
        raise ValueError('Camera confirmed a different selection; select again')


def make_http_handler(published_state, source, args, commands, control):
    class HTTP(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def respond(self,code,body,ctype='application/json'):
            self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Cache-Control','no-store')
            for k,v in cors_headers(self.headers.get('Origin'),args.allow_origin).items():self.send_header(k,v)
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_OPTIONS(self):
            # Browser preflight from an explicitly allowed page (never a wildcard).
            if self.path not in ('/api/state','/api/command','/api/heartbeat') or not cors_headers(self.headers.get('Origin'),args.allow_origin):self.respond(403,b'{}');return
            self.respond(204,b'')
        def do_GET(self):
            if self.path=='/api/state':self.respond(200,json.dumps(published_state(),allow_nan=False).encode())
            elif self.path=='/':self.respond(200,(ROOT/'hosts/pc/demo.html').read_bytes(),'text/html; charset=utf-8')
            elif self.path.startswith('/vision/'):
                path=self.path[len('/vision'):]
                if not (path.startswith('/frame.jpg?') or path in ('/overview.jpg','/api/clips','/api/report')):self.respond(404,b'{}');return
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8101'+path,timeout=2) as r:
                        self.respond(200,r.read(3*1024*1024),r.headers.get('Content-Type','application/json'))
                except urllib.error.HTTPError as exc:self.respond(exc.code,b'{}')
                except Exception:self.respond(503,b'{}')
            else:self.respond(404,b'{}')
        def do_POST(self):
            # Local UI only; JSON plus origin check prevents cross-origin form commands.
            if self.path not in ('/api/command','/api/heartbeat') or self.headers.get('Content-Type')!='application/json':self.respond(400,b'{}');return
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}',*args.allow_origin):
                self.respond(403,b'{}');return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<2048:raise ValueError('Invalid body size')
                data=json.loads(self.rfile.read(n))
                if not isinstance(data,dict):raise ValueError('Expected object')
                session=control.session(self.headers.get('X-Garuda-Session'))
                if self.path=='/api/heartbeat':
                    control.heartbeat(session,time.monotonic())
                    self.respond(200,b'{"ok":true}');return
                if data.get('command')=='select' and source['kind']=='onboard' and type(data.get('id')) is not int:
                    raise ValueError('Onboard target ID must be an integer')
                if (source['kind']=='pi_onboard' or (data.get('command')=='source' and data.get('kind')=='pi_onboard')) and data.get('command') not in ('hold','land') and session is None:
                    raise ValueError('Live-camera simulation requires a named browser control lease')
                ticket=control.submit(data,session,time.monotonic())
                if data.get('command')=='select' and source['kind'] in ('fixed_stereo','mono_estimate'):
                    # Done here, not in the control tick, so a slow camera cannot stall the
                    # offboard heartbeat. Only a confirmed camera selection is queued.
                    published=published_state()
                    snap=dict(frame=published.get('frame',{}),stats={'instance':published.get('source_instance')})
                    if data.get('epoch',snap['frame'].get('epoch'))!=snap['frame'].get('epoch'):raise ValueError('Image source changed; select again')
                    try:camera_select(source['url'],snap,data['id'])
                    except (ValueError,KeyError,TypeError) as e:
                        self.respond(409,json.dumps({'error':str(e)}).encode());return
                commands.put_nowait(ticket);self.respond(202,json.dumps({'queued':True,'id':ticket['id']}).encode())
            except (ValueError,TypeError,KeyError,OSError,queue.Full) as exc:self.respond(400,json.dumps({'error':str(exc)}).encode())
    return HTTP


def main():
    if os.environ.get('GARUDA_SIM')!='1' or os.environ.get('ROS_DOMAIN_ID')!='42':
        raise SystemExit('Refused: requires GARUDA_SIM=1 and isolated ROS_DOMAIN_ID=42')
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import ExternalShutdownException
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from px4_msgs.msg import (OffboardControlMode,TrajectorySetpoint,VehicleCommand,
        VehicleCommandAck,VehicleOdometry,VehicleStatus,VehicleLandDetected)
    p=argparse.ArgumentParser();p.add_argument('--source',default='http://10.42.0.128:8098')
    p.add_argument('--port',type=int,default=8099)
    p.add_argument('--allow-origin',action='append',default=[],help='Extra browser origin allowed to read state and send simulator commands (e.g. the camera demo page http://10.42.0.128:8098). Default: none.')
    p.add_argument('--stereo-reacquire',action='store_true',help='fixed_stereo source only: allow the clothing-appearance return match to move the selection to a new track ID. Default off (operator reselects).')
    p.add_argument('--accept-preview-stereo-in-simulation',action='store_true',help='fixed_stereo source only: let the unvalidated stereo preview and the assumed camera pose move the SIMULATED vehicle. Default off.')
    p.add_argument('--accept-mono-estimate-in-simulation',action='store_true',help='Allow explicit mono_estimate source: schematic fixed-camera mapping, not measured depth. Simulation only.')
    p.add_argument('--accept-onboard-in-simulation',action='store_true',help='Explicit Pi shadow-command rehearsal with Gazebo pixels; mono box-height only.')
    p.add_argument('--accept-live-onboard-in-simulation',action='store_true',help='Live Pi body commands into OPEN-LOOP SITL; explicit source, Pi request, Takeoff and Follow required.')
    args=p.parse_args()
    presence=SitlPresence()
    commands=queue.Queue(maxsize=50);observations=queue.Queue(maxsize=1)
    published={};running=threading.Event();running.set()
    source={'kind':'pi','url':args.source,'generation':0};events=deque(maxlen=30)
    wanted={'id':None,'at':-math.inf}      # fixed_stereo: track the controller follows; the camera must measure the same one
    delivery=DeliveryCounts()
    control=ControlLease();onboard_selection=OnboardSelection();live_status=SenderStatus()

    def read_source():
        reader=ObservationClient()
        while running.is_set():
            selected=dict(source)
            try:
                if selected['kind']=='pi_onboard':live_status.send(selected['url'])
                data=read_with_selection(reader,onboard_selection,selected)
                if data is not None and selected['generation']==source['generation']:
                    delivery.frame('reader_frames',selected['generation'],data)
                    # Latest-state queue; control still has its independent 50Hz timer.
                    try:observations.get_nowait();delivery.overwrite()
                    except queue.Empty:pass
                    observations.put_nowait((selected['generation'],time.monotonic(),data))
                    # After an appearance return match the controller follows a new track ID; ask the
                    # camera to measure that one. Done here, never in the control tick.
                    if selected['kind']=='fixed_stereo' and wanted['id'] is not None and data.get('lock',{}).get('id')!=wanted['id'] \
                            and time.monotonic()-wanted['at']>=1 and any(t.get('id')==wanted['id'] and not t.get('coasting') for t in data.get('tracks',[])):
                        wanted['at']=time.monotonic()
                        try:camera_select(selected['url'],data,wanted['id'])
                        except ValueError as e:events.append(dict(time=time.strftime('%H:%M:%S'),event='camera_select_failed',reason=str(e)))
            except Exception:
                time.sleep(.1)  # bounded outage backoff; control stale watchdog continues
            if selected['kind']=='pi':time.sleep(.2)  # legacy remote Pi contract unchanged
    threading.Thread(target=read_source,daemon=True).start()

    class Demo(Node):
        def __init__(self):
            super().__init__('garuda_isolated_demo')
            self.live_gate=LiveGate();self.live_report=None;self.bench_turn=BenchTurn()
            self.core=DemoControl();self.core.accept_mono=bool(args.accept_mono_estimate_in_simulation);self.core.accept_onboard=bool(args.accept_onboard_in_simulation);self.pose=None;self.pose_at=-math.inf
            self.core.accept_preview=bool(args.accept_preview_stereo_in_simulation);self.core.reacquire=bool(args.stereo_reacquire)
            self.pose_history=PoseHistory();self.pose_match=None;self.reselection_logged=False
            self.status=None;self.status_at=-math.inf;self.landed=None;self.land_at=-math.inf
            self.phase='GROUND';self.since=time.monotonic();self.last_cmd=-math.inf
            self.ack=None;self.error='';self.hold_alt=1.5;self.start_z=None;self.target_z=None
            self.hold_xy=None;self.intent=(0.,0.,None)
            self.published_setpoint=None;self.published_at=None;self.publish_count=0
            qos=QoSProfile(depth=5,reliability=ReliabilityPolicy.BEST_EFFORT,
                           durability=DurabilityPolicy.VOLATILE)
            def topic(direction,name,cls):
                version=getattr(cls,'MESSAGE_VERSION',0)
                return '/garuda_sitl/fmu/'+direction+'/'+name+(f'_v{version}' if version else '')
            self.mode_pub=self.create_publisher(OffboardControlMode,topic('in','offboard_control_mode',OffboardControlMode),qos)
            self.sp_pub=self.create_publisher(TrajectorySetpoint,topic('in','trajectory_setpoint',TrajectorySetpoint),qos)
            self.cmd_pub=self.create_publisher(VehicleCommand,topic('in','vehicle_command',VehicleCommand),qos)
            self.create_subscription(VehicleOdometry,topic('out','vehicle_odometry',VehicleOdometry),self.on_pose,qos)
            self.create_subscription(VehicleStatus,topic('out','vehicle_status',VehicleStatus),self.on_status,qos)
            self.create_subscription(VehicleLandDetected,topic('out','vehicle_land_detected',VehicleLandDetected),self.on_land,qos)
            self.create_subscription(VehicleCommandAck,topic('out','vehicle_command_ack',VehicleCommandAck),self.on_ack,qos)
            self.control_period=.02  # 400 ms freshness + 20 ms tick leaves margin inside 450 ms test bound
            self.create_timer(self.control_period,self.tick)

        def on_pose(self,msg):
            now=time.monotonic_ns()
            if self.pose_history.add(now,msg.position,msg.q,getattr(msg,'timestamp_sample',None)):
                self.pose=msg;self.pose_at=now/1e9
        def on_status(self,msg):self.status=msg;self.status_at=time.monotonic()
        def on_land(self,msg):
            self.landed=msg.landed;self.land_at=time.monotonic()
        def land_report(self,now):
            # PX4 repeats VehicleLandDetected at 1 Hz even when its value stays the same.
            return self.landed if 0 <= now-self.land_at < 2 else None
        def on_ack(self,msg):
            if msg.target_system==250:
                self.ack={'command':int(msg.command),'result':int(msg.result)}
        def fresh(self,now):return self.pose is not None and self.status is not None and now-self.pose_at<.5 and now-self.status_at<2
        def armed(self):return self.status is not None and self.status.arming_state==VehicleStatus.ARMING_STATE_ARMED
        def send(self,command,p1=0.,p2=0.):
            m=VehicleCommand();m.timestamp=int(self.get_clock().now().nanoseconds/1000)
            m.command=command;m.param1=float(p1);m.param2=float(p2)
            m.target_system=4;m.target_component=1;m.source_system=250;m.source_component=1;m.from_external=True
            self.cmd_pub.publish(m)
        def action(self,a,now):
            command=a.get('command')
            if command not in ('source','replay','benchmark','select','release','gestures','settings','land','takeoff','takeoff_follow','hold','follow','circle','test_mode','bench_reset'):
                raise ValueError('Unknown command')
            events.append(dict(time=time.strftime('%H:%M:%S'),command=command))
            if command in ('test_mode','bench_reset'):
                grounded(self.fresh(now),self.armed(),self.land_report(now),self.phase)
                if command=='bench_reset':
                    self.bench_turn.reset();self.live_gate.reset();self.core.reset();return
                selected=a.get('mode')
                if selected not in ('bench','gazebo'):
                    raise ValueError('Real vehicle unavailable: verified camera mounting, calibration and FC readiness are not yet integrated')
                # Go through the same guarded source transition; it clears Follow and selection.
                self.action(dict(command='source',kind='pi_onboard' if selected=='bench' else 'gazebo'),now)
                return
            if command=='source':
                if not self.fresh(now) or self.armed() or self.land_report(now) is not True or self.phase not in ('GROUND','FAULT'):raise ValueError('Land before changing camera source')
                kind=a.get('kind')
                if kind not in ('pi','gazebo','replay','fixed_stereo','mono_estimate','onboard','pi_onboard'):raise ValueError('Unknown camera source')
                if kind=='onboard' and not self.core.accept_onboard:raise ValueError('Use --accept-onboard-in-simulation')
                if kind=='pi_onboard' and not args.accept_live_onboard_in_simulation:raise ValueError('Use --accept-live-onboard-in-simulation')
                if kind not in ('pi','fixed_stereo','mono_estimate','pi_onboard'):
                    config=dict(mode=kind)
                    if kind=='replay':config['clip']=a.get('clip')
                    req=urllib.request.Request('http://127.0.0.1:8101/api/config',data=json.dumps(config).encode(),headers={'Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req,timeout=1):pass
                    except Exception as exc:raise ValueError('Vision source unavailable: '+str(exc))
                else:
                    try:
                        req=urllib.request.Request('http://127.0.0.1:8101/api/config',data=b'{"paused":true}',headers={'Content-Type':'application/json'})
                        with urllib.request.urlopen(req,timeout=.5):pass
                    except Exception:pass  # Pi view remains usable without the optional PC detector.
                source.update(kind=kind,url=args.source if kind in ('pi','fixed_stereo','mono_estimate','pi_onboard') else 'http://127.0.0.1:8101',generation=source['generation']+1)
                self.core.reset();self.live_gate.reset();live_status.publish(None);self.error='';return
            if command in ('replay','benchmark'):
                if source['kind']!='replay' or self.armed():raise ValueError('Replay controls require replay mode and disarmed vehicle')
                config={k:a[k] for k in ('paused','rate') if k in a}
                if command=='benchmark':config={'benchmark':a.get('clip')}
                req=urllib.request.Request('http://127.0.0.1:8101/api/config',data=json.dumps(config).encode(),headers={'Content-Type':'application/json'})
                try:
                    with urllib.request.urlopen(req,timeout=1):pass
                except Exception as exc:raise ValueError(str(exc))
                return
            if command=='select':
                if source['kind']=='pi_onboard':raise ValueError('Select the person on the camera page, then request PX4 on the PC')
                if source['kind']=='onboard' and type(a.get('id')) is not int:raise ValueError('Onboard target ID must be an integer')
                select_command(self.core,a,now);return
            if command=='release':self.live_gate.drop('Selection released');self.core.reset();return
            if command in ('circle','gestures') and source['kind'] in ('onboard','pi_onboard'):raise ValueError('Body-command sources support explicit Follow/Hold only')
            if command=='gestures':self.core.gestures=bool(a.get('enabled'));self.core.gesture_key=None;return
            if command=='settings':
                for name,lo,hi in [('gap',.5,5),('speed',.2,2),('limit',3,15)]:
                    if name in a:
                        v=float(a[name])
                        if not math.isfinite(v):raise ValueError('Nonfinite setting')
                        setattr(self.core,name,max(lo,min(hi,v)))
                if 'direction' in a:self.core.direction=1 if a['direction']=='cw' else -1
                return
            if command=='land':
                if source['kind']=='pi_onboard':self.live_gate.drop('Landing requested')
                if self.armed():self.phase='LANDING';self.last_cmd=-math.inf;self.core.mode='hold'
                return
            if command in ('takeoff','takeoff_follow'):
                if source['kind']=='replay':raise ValueError('Recorded footage is analysis only; takeoff disabled')
                if self.phase=='LANDING':return
                if command=='takeoff' and self.armed():return
                if not self.fresh(now):raise ValueError('PX4 telemetry unavailable')
                if not self.armed():
                    if self.land_report(now) is not True:raise ValueError('Waiting for fresh ground contact from PX4')
                    self.start_z=float(self.pose.position[2]);self.target_z=self.start_z-self.hold_alt
                    self.core.home=tuple(map(float,self.pose.position[:2]))
                    self.phase='PRESTREAM';self.since=now;self.last_cmd=-math.inf;self.hold_xy=tuple(map(float,self.pose.position[:2]))
                self.core.mode='follow' if command=='takeoff_follow' else 'hold';self.error='';return
            if command in ('hold','follow','circle'):
                if source['kind']=='pi_onboard' and command=='hold':self.live_gate.drop('Hold requested')
                self.core.mode=command;self.core.angle=None;self.hold_xy=None
                if command=='hold' and self.phase in ('PRESTREAM','REQUESTING','FLYING'):
                    if self.phase=='PRESTREAM' and not self.armed():
                        # No arm command has been sent yet: cancel startup entirely.
                        self.phase='GROUND';self.target_z=None
                    elif self.fresh(now):
                        # Arming or a climb may be in flight. Do not claim disarmed,
                        # but cancel the altitude goal and hold at the current pose.
                        self.target_z=float(self.pose.position[2])
                        self.hold_xy=tuple(map(float,self.pose.position[:2]))
                    else:
                        self.phase='FAULT'
                        raise ValueError('Cannot confirm takeoff hold: PX4 telemetry unavailable')
                if self.error=='Browser control lease expired; explicitly resume':self.error=''
        def tick(self):
            nonlocal published
            now=time.monotonic()
            self.intent=(0.,0.,None);self.core.command_yaw_rate=0.;self.pose_match=None
            if control.consume_disconnect(now):
                try:self.action({'command':'hold'},now)
                except ValueError as exc:self.error=str(exc)
                self.core.gestures=False
                if source['kind']=='onboard':self.core.boundary('Browser control lease expired')
                if source['kind']=='pi_onboard':self.live_gate.drop('Browser control lease expired')
                self.error='Browser control lease expired; explicitly resume'
            if not observations.empty():
                generation,received,data=observations.get_nowait()
                if generation==source['generation']:
                    delivery.frame('controller_frames',generation,data)
                    if isinstance(data.get('age_ms'),(int,float)):data['age_ms']+=(now-received)*1000
                    self.snapshot_generation=generation
                    gesture=self.core.receive(data,now)
                    if gesture and control.gestures_allowed(now):
                        try:self.action({'command':gesture},now)
                        except ValueError as exc:self.error=str(exc)
            refusal=onboard_selection.consume_refusal(source,self.core)
            if refusal:events.append(dict(time=time.strftime('%H:%M:%S'),event='onboard_selection_refused',reason=refusal))
            while not commands.empty():
                error=control.execute(commands.get_nowait(),now,self.action)
                if error:self.error=error
            fresh=self.fresh(now);armed=self.armed();nav=int(self.status.nav_state) if self.status else None
            presence.tick(now,fresh)
            display_person=None
            live_command=None;live_sent=False
            if source['kind']=='pi_onboard':
                error=live_status.take_error(self.live_gate.observed)
                if error:self.live_gate.drop(error)
                age=self.core.snapshot.get('age_ms')
                age=age+(now-self.core.received)*1000 if type(age) in (int,float) else None
                live_command=self.live_gate.evaluate(self.core.snapshot,age,(now-self.core.progress)*1000,args.accept_live_onboard_in_simulation)
                if not fresh:self.live_gate.drop('PX4 telemetry stale');live_command=None
                display_person=onboard_visual.project(self.core.snapshot,age,(now-self.core.progress)*1000,
                    list(map(float,self.pose.position)) if self.pose else None, list(map(float,self.pose.q)) if self.pose else None,fresh)
            if fresh and self.error=='PX4 telemetry unavailable':self.error=''   # do not keep showing it once telemetry is back
            if self.phase=='LANDING':
                if fresh and not armed and self.land_report(now) is True:self.phase='GROUND'
                elif fresh and now-self.last_cmd>1:
                    self.send(VehicleCommand.VEHICLE_CMD_NAV_LAND);self.last_cmd=now
            active=self.phase in ('PRESTREAM','REQUESTING','FLYING')
            if active and not fresh:
                # Stop offboard heartbeat: PX4's configured offboard-loss action takes over.
                self.error='PX4 telemetry stale • offboard heartbeat stopped'
                self.phase='FAULT'
            elif active:
                if self.phase=='PRESTREAM' and now-self.since>=2:self.phase='REQUESTING'
                if self.phase=='REQUESTING':
                    if armed and nav==VehicleStatus.NAVIGATION_STATE_OFFBOARD:self.phase='FLYING'
                    elif now-self.since>12:self.phase='FAULT';self.error='Offboard/arm not confirmed; inspect PX4 acknowledgement'
                    elif now-self.last_cmd>1:
                        self.send(VehicleCommand.VEHICLE_CMD_DO_SET_MODE,1,6)
                        self.send(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,1)
                        self.last_cmd=now
                if self.phase=='FLYING' and (not armed or nav!=VehicleStatus.NAVIGATION_STATE_OFFBOARD):
                    self.phase='GROUND' if not armed else 'FAULT';self.error='PX4 left offboard'
                if self.phase in ('PRESTREAM','REQUESTING','FLYING'):
                    mode=OffboardControlMode();mode.timestamp=int(self.get_clock().now().nanoseconds/1000);mode.position=True;mode.velocity=True
                    sp=TrajectorySetpoint();sp.timestamp=mode.timestamp
                    vn,ve,yaw=(0.,0.,None)
                    if self.phase=='FLYING' and abs(float(self.pose.position[2])-self.target_z)<.3:
                        delivery.frame('evaluated_frames',getattr(self,'snapshot_generation',None),self.core.snapshot)
                        match=self.pose_history.match(self.core.snapshot.get('frame',{}),int(now*1e9))
                        self.pose_match=dict(receipt_ns=match.receipt_ns,skew_ns=match.skew_ns,method='nearest-PC-receipt-not-exposure') if match else None
                        observed=(match.position,match.quaternion) if match else None
                        if source['kind']=='pi_onboard':
                            if live_command is not None and self.core.mode=='follow':
                                move,why=body_horizontal(live_command,list(map(float,self.pose.q)),list(map(float,self.pose.position)),self.core.home,self.core.speed,self.core.limit)
                                if move is None:self.live_gate.drop(why)
                                else:
                                    w,x,y,z=map(float,self.pose.q)
                                    heading=math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
                                    if self.bench_turn.step(now,heading):
                                        # Bench camera cannot respond to simulated movement: yaw-only trial.
                                        vn,ve=0.,0.;self.core.command_yaw_rate=move[2];live_sent=True
                                    else:
                                        self.live_gate.drop(self.bench_turn.reason);self.core.mode='hold'

                        else:
                            vn,ve,yaw=self.core.velocity(list(map(float,self.pose.position)),now,self.control_period,list(map(float,self.pose.q)),observation_pose=observed)
                    self.intent=(vn,ve,yaw)
                    if vn==0 and ve==0:
                        if self.hold_xy is None:self.hold_xy=tuple(map(float,self.pose.position[:2]))
                        sp.position=[*self.hold_xy,self.target_z];sp.velocity=[math.nan]*3
                    else:
                        self.hold_xy=None;sp.position=[math.nan,math.nan,self.target_z];sp.velocity=[vn,ve,math.nan]
                    sp.acceleration=[math.nan]*3;sp.jerk=[math.nan]*3;sp.yaw=yaw if yaw is not None else math.nan;sp.yawspeed=float(self.core.command_yaw_rate)
                    self.mode_pub.publish(mode);self.sp_pub.publish(sp)
                    finite=lambda v:float(v) if math.isfinite(float(v)) else None
                    self.publish_count+=1;self.published_at=now
                    self.published_setpoint={'position_ned_m':[finite(v) for v in sp.position],
                        'velocity_ned_m_s':[finite(v) for v in sp.velocity],
                        'yaw_rad':finite(sp.yaw),'yaw_rate_rad_s':finite(sp.yawspeed)}
            tracks=self.core.snapshot.get('tracks',[])
            if source['kind']=='pi_onboard':
                if self.live_gate.was_sending and not live_sent:self.live_gate.drop('Setpoint forwarding stopped')
                self.live_report=self.live_gate.report(live_sent)
                live_status.publish(self.live_report)
                self.core.target_id=self.live_report['target_id']
                self.core.reason=self.live_report['reason'];self.core.target=None
                self.core.lock_state='LOCKED' if live_command is not None and self.live_gate.grant else 'RESELECT'
                self.core.requires_reselection=self.live_gate.grant is None
            else:
                self.live_report=None;live_status.publish(None);self.core.track(now)
            onboard_selection.publish(self.core,source['kind']=='onboard')
            wanted['id']=self.core.target_id if source['kind']=='fixed_stereo' and self.core.reacquire and self.core.lock_state=='LOCKED' else None
            if self.core.requires_reselection and not self.reselection_logged:
                age=self.core.snapshot.get('age_ms')
                events.append(dict(time=time.strftime('%H:%M:%S'),event='reselection_required',
                    reason=self.core.reason,control_monotonic=now,
                    last_distinct_frame_monotonic=self.core.progress if math.isfinite(self.core.progress) else None,
                    intent=self.intent,intent_yaw_rate=self.core.command_yaw_rate,
                    epoch=self.core.snapshot.get('frame',{}).get('epoch'),
                    frame_seq=self.core.snapshot.get('frame',{}).get('seq'),
                    perception_age_ms=age+(now-self.core.received)*1000 if isinstance(age,(int,float)) else None))
            self.reselection_logged=self.core.requires_reselection
            published={'phase':self.phase,'armed':armed,'nav_state':nav,'landed':self.land_report(now),'landed_fresh':self.land_report(now) is not None,
                'position':list(map(float,self.pose.position)) if self.pose else None,'telemetry_fresh':fresh,
                'control_timing':{'tick_monotonic':now,
                    'last_distinct_frame_monotonic':self.core.progress if math.isfinite(self.core.progress) else None},
                'command_monitor':{'destination':'PX4 SITL · system 4 · simulation only','count':self.publish_count,
                    'age_ms':(now-self.published_at)*1000 if self.published_at is not None else None,
                    'publishing':self.published_at==now,'setpoint':self.published_setpoint},
                'control':control.state(now),'intent':self.intent,'intent_yaw_rate':self.core.command_yaw_rate,'pose_match':self.pose_match,'delivery':delivery.snapshot(),
                'attitude':list(map(float,self.pose.q)) if self.pose else None,
                'altitude':self.start_z-float(self.pose.position[2]) if self.pose and self.start_z is not None else 0,
                'target_id':self.core.target_id,'target':self.core.target,'behavior':self.core.mode,
                'reason':self.core.reason,'error':self.error,'ack':self.ack,'gestures':self.core.gestures,
                'tracks':tracks,'frame':self.core.snapshot.get('frame',{}),'lock_state':self.core.lock_state,
                'gesture_pending':self.core.gesture_pending,'events':list(events),
                'requires_reselection':self.core.requires_reselection,'return_candidate':self.core.candidate,'reacquired':self.core.reacquired,
                'metrics':self.core.snapshot.get('metrics',{}),'camera':self.core.snapshot.get('camera',{}),
                'vision_error':self.core.snapshot.get('error',''),'rtf':self.core.snapshot.get('rtf'),
                'replay':self.core.snapshot.get('replay'),'paused':self.core.snapshot.get('paused',False),
                'benchmark':self.core.snapshot.get('benchmark','idle'),
                'detector':self.core.snapshot.get('detector',{}),'perception_age_ms':self.core.snapshot.get('age_ms',0)+(now-self.core.received)*1000 if isinstance(self.core.snapshot.get('age_ms'),(int,float)) else None,
                'onboard_visual':display_person,'companion_presence':presence.state(),
                'test_mode':'bench' if source['kind']=='pi_onboard' else 'gazebo' if source['kind']=='gazebo' else 'other',
                'bench_trial':self.bench_turn.state(now),
                'live_onboard':self.live_report,'accept_live_onboard_in_simulation':args.accept_live_onboard_in_simulation,
                'onboard_selection':onboard_selection.state(),'onboard':self.live_gate.command if source['kind']=='pi_onboard' else current_command(self.core),'selection_generation':self.core.selection_generation,'accept_onboard_in_simulation':self.core.accept_onboard,
                'onboard_provenance':self.core.snapshot.get('onboard_provenance'),
                'mono_estimate':mono_estimate.provenance(self.core.accept_mono) if source['kind']=='mono_estimate' else None,
                'fixed_stereo':self.core.snapshot.get('fixed_stereo'),'fixed_camera':dict(self.core.camera) if source['kind']=='fixed_stereo' else None,
                'accept_preview_stereo_in_simulation':self.core.accept_preview,'stereo_reacquire':self.core.reacquire,'source_instance':self.core.snapshot.get('stats',{}).get('instance'),
                'source':source['url'],'source_kind':source['kind'],'gap':self.core.gap,'speed':self.core.speed,'limit':self.core.limit,
                'home':self.core.home,'direction':'cw' if self.core.direction==1 else 'ccw'}

    HTTP=make_http_handler(lambda:published,source,args,commands,control)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),HTTP)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    rclpy.init();node=Demo();print(f'Isolated SITL dashboard http://127.0.0.1:{args.port}',flush=True)
    try:rclpy.spin(node)
    except (KeyboardInterrupt,ExternalShutdownException):pass
    finally:running.clear();presence.close();node.destroy_node();rclpy.try_shutdown();server.shutdown()

if __name__=='__main__':main()
