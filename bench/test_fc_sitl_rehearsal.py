"""Offline runner checks. No socket creation, subprocess or PX4 commands."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from fc_sitl_rehearsal import ground, snapshot, decode_setpoint, zero_latency
from live_onboard import validate
import fc_sitl_rehearsal as runner


def test_scripted_fixture_is_valid_but_identity_fault_is_not():
    s=snapshot(2,1,'fc',.2,.15)
    command,why=validate(s,0,0)
    assert command and why is None and command['range_source']=='box_height'
    assert validate(snapshot(3,1,'fc',.2,.15,False),0,0)[0] is None


def test_preflight_requires_positive_ground_evidence():
    assert ground(dict(telemetry_fresh=True,landed=True,armed=False))
    for s in ({},dict(telemetry_fresh=False,landed=True,armed=False),dict(telemetry_fresh=True,landed=False,armed=False),dict(telemetry_fresh=True,landed=True,armed=True)):
        assert not ground(s)


def message(**kw):
    fields=dict(target_system=4,target_component=1,coordinate_frame=8,type_mask=1479,vx=.2,vy=0.,vz=0.,yaw_rate=.15)
    fields.update(kw)
    return SimpleNamespace(get_type=lambda:'SET_POSITION_TARGET_LOCAL_NED',**fields)


def test_wire_audit_rejects_wrong_destination_and_unbounded_commands():
    assert decode_setpoint(message())==dict(forward=.2,yaw=.15)
    for kw in (dict(target_system=1),dict(coordinate_frame=1),dict(type_mask=0),dict(vx=.4),dict(vy=.1),dict(vz=.1),dict(yaw_rate=float('nan'))):
        with pytest.raises(RuntimeError): decode_setpoint(message(**kw))


def test_fault_scoring_cannot_pass_empty_late_or_moving_evidence():
    zero=dict(forward=0.,yaw=0.); move=dict(forward=.2,yaw=0.)
    assert zero_latency([(10.1,zero)],10,.45)==pytest.approx(.1)
    for points in ([],[(10.6,zero)],[(10.1,move)],[(9.,zero)]):
        with pytest.raises(RuntimeError): zero_latency(points,10,.45)


def test_default_invocation_only_prints_plan():
    p=subprocess.run([sys.executable,str(Path(__file__).with_name('fc_sitl_rehearsal.py'))],capture_output=True,text=True,check=True)
    plan=json.loads(p.stdout)
    assert plan['sends'] is False and len(plan['plan'])==6 and len(plan['bridge_sha256'])==64


def test_cleanup_pumps_missing_telemetry_sends_land_retries_and_waits_for_ground(monkeypatch):
    clock=[0.]; sent=[]; ticks=[]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runner.time,'sleep',lambda duration:clock.__setitem__(0,clock[0]+duration))
    t=runner.Trial.__new__(runner.Trial); t.gen=0; t.log=lambda *a,**k:None
    def telemetry(name):
        if clock[0]<.2: raise RuntimeError('stale')
        if name=='HEARTBEAT': return dict(base_mode=128 if clock[0]<2.2 else 0)
        if clock[0]<2.3: raise KeyError('not received yet')
        return dict(landed_state=1)
    t.wire=SimpleNamespace(address=('127.0.0.1',14583),messages={},mav=SimpleNamespace(MAV_CMD_NAV_LAND=21),
        telemetry=telemetry,command=lambda cmd:sent.append((clock[0],cmd)))
    t.tick=lambda stage,cleanup=False:ticks.append((clock[0],cleanup))
    assert t.cleanup_land(True,timeout=3)
    assert len(sent)>=2 and sent[0][0]==0 and all(cmd==21 for _,cmd in sent)
    assert ticks and all(cleanup for _,cleanup in ticks) and clock[0]>=2.3


def test_cleanup_never_claims_ground_when_telemetry_stays_missing(monkeypatch):
    clock=[0.]; sent=[]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runner.time,'sleep',lambda d:clock.__setitem__(0,clock[0]+d))
    t=runner.Trial.__new__(runner.Trial);t.gen=0;t.log=lambda *a,**k:None;t.tick=lambda *a,**k:None
    def missing(name): raise RuntimeError('missing')
    t.wire=SimpleNamespace(address=('127.0.0.1',14583),messages={},mav=SimpleNamespace(MAV_CMD_NAV_LAND=21),
        telemetry=missing,command=sent.append)
    with pytest.raises(RuntimeError,match='not confirmed'):t.cleanup_land(True,timeout=.2)
    assert sent==[21]


def test_wait_tolerates_temporarily_missing_fields(monkeypatch):
    clock=[0.]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runner.time,'sleep',lambda d:clock.__setitem__(0,clock[0]+d))
    t=runner.Trial.__new__(runner.Trial);t.log=lambda *a,**k:None;t.tick=lambda *a,**k:None
    def predicate():
        if clock[0]<.1:raise KeyError('not yet')
        return True
    t.wait('temporary missing',predicate,1)
    assert clock[0]>=.1


def test_process_record_updates_stale_pid_and_refuses_another_live_owner(tmp_path,monkeypatch):
    path=tmp_path/'.runtime/processes/dashboard.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(pid=10,identity='old')))
    monkeypatch.setattr(runner,'process_identity',lambda pid:{20:'new',30:'other'}.get(pid))
    runner.update_process_record(tmp_path,10,20)
    assert json.loads(path.read_text())==dict(pid=20,identity='new')
    path.write_text(json.dumps(dict(pid=30,identity='other')))
    with pytest.raises(RuntimeError,match='Another live'):runner.update_process_record(tmp_path,10,20)


def test_runner_explicit_hold_follow_and_silence_are_distinct(monkeypatch):
    from fc_gate import FcGate
    clock=[0.]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runner.time,'sleep',lambda d:clock.__setitem__(0,clock[0]+d))
    wire=SimpleNamespace(packets=[],setpoints=[])
    class GateBridge:
        def __init__(self,*a,**kw):self.gate=FcGate();self.receive_only=False
        def tick(self,s,a,p):
            out=self.gate.step(s,a,p,dict(offboard=True,armed=False))
            if out['stream']:
                wire.packets.append(clock[0]);wire.setpoints.append((clock[0],dict(forward=out['forward_vel'],yaw=out['yaw_rate'])))
            return out
    monkeypatch.setattr(runner,'Bridge',GateBridge)
    t=runner.Trial(wire,lambda *a,**kw:None)
    t.period('baseline',.2);assert not wire.packets
    t.open_session();assert wire.packets and t.request=='fc_hold' and t.last['stream']
    t.grant(forward=.2,yaw=0);assert t.last['state']=='sending'
    t.bridge.gate.end_session('loss');t.silent('no restart',.2)
    with pytest.raises(RuntimeError,match='not evidenced'):t.held('silence is not zeros',.1)
    t.open_session();t.held('zeros now',.2)


def test_attitude_fault_does_not_hide_heartbeat_from_bridge():
    ref=pytest.importorskip('pymavlink.dialects.v20.common')
    encoder=ref.MAVLink(None,srcSystem=4,srcComponent=1)
    a=encoder.attitude_encode(100,0,0,0,0,0,0).pack(encoder)
    hb=encoder.heartbeat_encode(2,12,0,6<<16,4,3).pack(encoder)
    wire=runner.Wire.__new__(runner.Wire)
    wire.sock=SimpleNamespace(recvfrom=lambda n:(a+hb,('127.0.0.1',14583)))
    wire.parser=ref.MAVLink(None);wire.address=('127.0.0.1',14583)
    wire.messages={};wire.parameters={};wire.attitude_progress=[];wire.log=lambda *a,**kw:None
    wire.suppress_heartbeat=False;wire.suppress_attitude=True
    data,address=wire.recvfrom(4096)
    messages=ref.MAVLink(None).parse_buffer(data)
    assert [m.get_type() for m in messages]==['HEARTBEAT']
    assert 'ATTITUDE' in wire.messages and wire.attitude_progress


def test_altitude_band_and_settle_reset_on_missing_telemetry(monkeypatch):
    assert runner.altitude_in_band(-2.5,-2.5)
    assert not runner.altitude_in_band(-2.2,-2.5)
    clock=[0.];missing=[False]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    def telemetry(name):
        if missing[0]:raise RuntimeError('stale')
        return dict(z=-2.5,vz=0.)
    t=runner.Trial.__new__(runner.Trial);t.wire=SimpleNamespace(telemetry=telemetry)
    def wait(stage,predicate,timeout):
        assert not predicate();clock[0]=1.;assert not predicate()
        missing[0]=True;clock[0]=1.5
        with pytest.raises(RuntimeError):predicate()
        missing[0]=False;clock[0]=2.;assert not predicate()
        clock[0]=3.;assert not predicate()
        clock[0]=4.;assert predicate()
    t.wait=wait;t.wait_settled(-2.5)
