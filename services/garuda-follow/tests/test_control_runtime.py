"""Execute the real main/tick/HTTP path with ROS transport stubbed; no Pi or PX4 contacted."""
import json
import sys
import time
from types import ModuleType, SimpleNamespace
from urllib.request import Request, urlopen
import sitl_demo


def test_browser_expiry_holds_real_control_loop_and_requires_explicit_resume(monkeypatch):
    modules={name:ModuleType(name) for name in ('rclpy','rclpy.node','rclpy.executors','rclpy.qos','px4_msgs','px4_msgs.msg')}
    sent=[];timers=[]
    class Node:
        def __init__(self,*a):pass
        def create_publisher(self,*a):return SimpleNamespace(publish=lambda msg:sent.append((a[1],msg)))
        def create_subscription(self,*a):pass
        def create_timer(self,*a):timers.append(a)
        def get_clock(self):return SimpleNamespace(now=lambda:SimpleNamespace(nanoseconds=1))
        def destroy_node(self):pass
    class Message:
        MESSAGE_VERSION=0
        ARMING_STATE_ARMED=2
        NAVIGATION_STATE_OFFBOARD=14
    modules['rclpy.node'].Node=Node
    modules['rclpy.executors'].ExternalShutdownException=type('ExternalShutdownException',(Exception,),{})
    modules['rclpy.qos'].QoSProfile=lambda **kw:None
    modules['rclpy.qos'].ReliabilityPolicy=SimpleNamespace(BEST_EFFORT=1)
    modules['rclpy.qos'].DurabilityPolicy=SimpleNamespace(VOLATILE=1)
    for name in ('OffboardControlMode','TrajectorySetpoint','VehicleCommand','VehicleCommandAck','VehicleOdometry','VehicleStatus','VehicleLandDetected'):
        setattr(modules['px4_msgs.msg'],name,Message)
    for name,mod in modules.items():monkeypatch.setitem(sys.modules,name,mod)
    monkeypatch.setenv('GARUDA_SIM','1');monkeypatch.setenv('ROS_DOMAIN_ID','42')
    monkeypatch.setattr(sys,'argv',['sitl_demo.py','--port','0','--accept-live-onboard-in-simulation'])
    # Never perform a camera request from the background reader.
    monkeypatch.setattr(sitl_demo.ObservationClient,'read',lambda *a:time.sleep(.01))
    monkeypatch.setattr(sitl_demo,'SitlPresence',lambda:SimpleNamespace(tick=lambda *a:None,state=lambda:{},close=lambda:None))
    selections=[];real_selection=sitl_demo.OnboardSelection
    def selection():
        s=real_selection();s.sync=lambda *a:None;selections.append(s);return s
    monkeypatch.setattr(sitl_demo,'OnboardSelection',selection)
    leases=[];real_lease=sitl_demo.ControlLease
    def lease():
        c=real_lease();leases.append(c);return c
    monkeypatch.setattr(sitl_demo,'ControlLease',lease)
    servers=[];real_server=sitl_demo.ThreadingHTTPServer
    def server(*args):
        s=real_server(*args);servers.append(s);return s
    monkeypatch.setattr(sitl_demo,'ThreadingHTTPServer',server)
    def spin(node):
        c=leases[0]
        assert node.control_period==.02
        assert len(timers)==1 and timers[0][0]==.02 and timers[0][1]==node.tick
        node.status=SimpleNamespace(arming_state=2,nav_state=14)
        node.pose=SimpleNamespace(position=[0.,0.,-1.5],q=[1.,0.,0.,0.])
        node.pose_at=node.status_at=time.monotonic()
        node.status.arming_state=1;node.phase='PRESTREAM';node.target_z=-3.
        node.action({'command':'hold'},time.monotonic())
        assert node.phase=='GROUND' and node.target_z is None
        node.phase='REQUESTING';node.target_z=-3.
        node.action({'command':'hold'},time.monotonic())
        assert node.phase=='REQUESTING' and node.target_z==node.pose.position[2]
        assert node.core.mode=='hold'  # possibly in-flight arm request, but no remaining climb goal
        node.status.arming_state=2;node.phase='FLYING';node.start_z=0.;node.target_z=-1.5
        def post(action):
            req=Request(f'http://127.0.0.1:{servers[0].server_port}/api/command',data=json.dumps(action).encode(),headers={'Content-Type':'application/json','X-Garuda-Session':'tab'},method='POST')
            with urlopen(req,timeout=1) as r:return json.load(r)
        receipt=post({'command':'follow'});node.tick()
        assert node.core.mode=='follow' and c.state(time.monotonic())['last_command']['id']==receipt['id']
        node.core.gestures=True;node.target_z=-3.0  # already FLYING but still climbing
        c.deadline=time.monotonic()-1
        node.tick()
        assert node.core.mode=='hold' and node.core.gestures is False
        assert node.target_z==node.pose.position[2]  # Hold arrests the climb too
        assert node.intent[:2]==(0.,0.)
        # Polling does not reclaim ownership or resume. The next explicit command can.
        with urlopen(f'http://127.0.0.1:{servers[0].server_port}/api/state',timeout=1) as r:
            state=json.load(r)
        assert state['control']['expired'] and state['behavior']=='hold'
        assert isinstance(state['control_timing']['tick_monotonic'],float)
        assert state['control_timing']['last_distinct_frame_monotonic'] is None
        node.tick();assert node.core.mode=='hold'
        post({'command':'follow'});node.tick();assert node.core.mode=='follow' and node.error==''
        # Exercise the actual PX4 message fields for the new body-command source.
        from .test_onboard_source import snap
        node.core.accept_onboard=True
        now=time.monotonic();node.core.receive(snap(generation=node.core.selection_generation+1),now);node.core.select(7);node.core.mode='follow'
        node.tick()
        sp=next(msg for topic,msg in reversed(sent) if '/trajectory_setpoint' in topic)
        assert sp.velocity[:2]==[.3,0.] and sp.yawspeed==.2
        assert sp.position[2]==node.target_z and sp.yaw!=sp.yaw  # yaw angle deliberately NaN, use yaw rate
        node.action({'command':'hold'},time.monotonic());node.tick()
        sp=next(msg for topic,msg in reversed(sent) if '/trajectory_setpoint' in topic)
        assert sp.yawspeed==0. and all(v!=v for v in sp.velocity)
        # Actual HTTP release/queued select -> tick -> async producer intent.
        import io
        node.phase='GROUND';node.status.arming_state=1
        with monkeypatch.context() as m:
            m.setattr(sitl_demo.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(b'{"ok":true}'))
            node.action({'command':'source','kind':'onboard'},time.monotonic())
        node.core.receive(snap(generation=node.core.selection_generation+1),time.monotonic())
        from urllib.error import HTTPError
        import pytest
        for invalid in (True,7.9,'7'):
            with pytest.raises(HTTPError) as e:post({'command':'select','id':invalid})
            assert e.value.code==400
        post({'command':'select','id':7});node.tick()
        generation=node.core.selection_generation
        assert selections[0].desired['onboard_select']==7
        post({'command':'release'});node.tick()
        assert selections[0].desired['onboard_select'] is None and node.core.target_id is None
        node.core.receive(snap(generation=node.core.selection_generation+1),time.monotonic())
        post({'command':'select','id':8});node.tick()  # invalid queued selection has no producer side effect
        assert selections[0].desired['onboard_select'] is None
        post({'command':'select','id':7});node.tick()
        assert selections[0].desired['selection_gen']>generation
        c.deadline=time.monotonic()-1;node.tick()
        assert node.core.requires_reselection and selections[0].desired['onboard_select'] is None
        with urlopen(f'http://127.0.0.1:{servers[0].server_port}/api/state',timeout=1) as r:
            state=json.load(r)
        event=next(e for e in reversed(state['events']) if e.get('event')=='reselection_required')
        assert event['control_monotonic']==state['control_timing']['tick_monotonic']
        assert event['intent'][:2]==[0.,0.] and event['intent_yaw_rate']==0.
        assert event['last_distinct_frame_monotonic']<=event['control_monotonic']
        # Live Pi is a separate source, using the ONE existing setpoint publisher.
        from .test_live_onboard import snapshot
        node.phase='GROUND';node.status.arming_state=1;node.landed=True
        node.pose_at=node.status_at=time.monotonic()
        with monkeypatch.context() as m:
            m.setattr(sitl_demo.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(b'{"ok":true}'))
            node.action({'command':'source','kind':'pi_onboard'},time.monotonic())
        node.core.receive(snapshot(),time.monotonic());node.tick()  # record startup baseline
        node.core.receive(snapshot(2,1,'sim'),time.monotonic());node.tick()
        assert node.live_report['state']=='ready' and node.live_report['open_loop']
        node.phase='FLYING';node.status.arming_state=2;node.target_z=-1.5
        post({'command':'follow'});node.tick()
        sp=next(msg for topic,msg in reversed(sent) if '/trajectory_setpoint' in topic)
        assert all(v!=v for v in sp.velocity) and sp.yawspeed==.2  # bench is yaw-only
        assert node.live_report['state']=='sending'
        assert node.published_setpoint['yaw_rate_rad_s']==sp.yawspeed
        assert node.published_setpoint['position_ned_m']==[None if not __import__('math').isfinite(v) else float(v) for v in sp.position]
        assert node.publish_count>0 and node.published_at is not None
        post({'command':'hold'});node.tick()
        assert node.intent[:2]==(0.,0.) and node.core.command_yaw_rate==0
        node.core.receive(snapshot(3,1,'sim'),time.monotonic())
        post({'command':'follow'});node.tick()
        assert node.intent[:2]==(0.,0.)  # cannot resurrect old request
        node.core.receive(snapshot(4,2,'sim'),time.monotonic());node.tick()
        assert node.intent[:2]==(0.,0.) and node.core.command_yaw_rate==.2
        node.pose.position=[10.,0.,-1.5];node.tick()
        assert node.intent[:2]==(0.,0.) and node.core.command_yaw_rate==0 and node.live_gate.grant is None
        node.pose.position=[0.,0.,-1.5]
        node.core.receive(snapshot(5,2,'sim'),time.monotonic());node.tick()
        assert node.intent[:2]==(0.,0.)  # returning inside fence does not resurrect grant
        node.core.receive(snapshot(6,3,'sim'),time.monotonic());node.tick()
        assert node.intent[:2]==(0.,0.) and node.core.command_yaw_rate==.2
        # Exercise the real publisher at the budget boundary, not only the budget helper.
        node.bench_turn.started=time.monotonic()-3.1
        node.tick()
        sp=next(msg for topic,msg in reversed(sent) if '/trajectory_setpoint' in topic)
        assert sp.yawspeed==0 and node.core.mode=='hold' and node.bench_turn.reason
        node.core.receive(snapshot(7,4,'sim'),time.monotonic())
        post({'command':'follow'});node.tick()
        assert node.core.command_yaw_rate==0 and node.core.mode=='hold'
        for action in ({'command':'bench_reset'},{'command':'test_mode','mode':'gazebo'}):
            post(action);node.tick()
            assert c.last_command['applied'] is False and node.bench_turn.reason
        node.phase='GROUND';node.status.arming_state=1;node.landed=True
        post({'command':'test_mode','mode':'real'});node.tick()
        assert not c.last_command['applied'] and 'Real vehicle unavailable' in c.last_command['error']
        post({'command':'bench_reset'});node.tick()
        assert c.last_command['applied'] and node.bench_turn.started is None and node.core.mode=='hold'
        with monkeypatch.context() as m:
            m.setattr(sitl_demo.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(b'{"ok":true}'))
            node.action({'command':'test_mode','mode':'gazebo'},time.monotonic())
        assert node.core.target_id is None and node.core.mode=='hold'
        c.deadline=time.monotonic()-1;node.tick()
        assert node.intent[:2]==(0.,0.) and node.live_gate.grant is None
        node.phase='REQUESTING';node.pose_at=-float('inf');c.deadline=time.monotonic()-1
        node.tick()
        assert node.phase=='FAULT' and node.core.mode=='hold'  # missing telemetry must not crash expiry handling
    modules['rclpy'].init=lambda:None
    modules['rclpy'].try_shutdown=lambda:None
    modules['rclpy'].spin=spin
    sitl_demo.main()
