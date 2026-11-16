import copy
import math
import pytest
from sitl_demo_core import DemoControl
from onboard_source import PROVENANCE


def snap(seq=1, generation=1):
    return dict(kind='onboard',age_ms=50,onboard_provenance=PROVENANCE,
        detector=dict(engine='hailo10h-rpc'),stats=dict(instance='pi-rpc',capabilities=dict(mount='vehicle',camera='gazebo')),
        frame=dict(epoch='cam',seq=seq,width=1280,height=720),
        tracks=[dict(id=7,label='person',coasting=False,conf=.9,x=600,y=200,w=150,h=200)],
        onboard=dict(selection_gen=generation,valid=True,transmit=False,frame_seq=seq,frame_epoch='cam',instance='pi-rpc',target_id=7,
            range_source='box_height',range_m=None,forward_vel=.3,yaw_rate=.2,vertical_vel=0))


def core(accepted=True):
    c=DemoControl();c.accept_onboard=accepted;c.receive(snap(),0);c.select(7);c.mode='follow'
    return c


def step(c,s=None,now=.05,q=None,pos=None):
    if s is not None:c.receive(s,now)
    return c.velocity(pos or [0,0,-1.5],now,.05,q or [1,0,0,0])


def test_explicit_source_and_body_to_ned_yaw_rate():
    c=core(False);assert step(c)==(0,0,None)
    assert '--accept-onboard' in c.reason
    c=core();assert step(c)==(.3,0,None) and c.command_yaw_rate==.2
    q=[math.sqrt(.5),0,0,math.sqrt(.5)]
    assert step(c,q=q)[:2]==pytest.approx((0,.3))
    assert c.target is None  # body command, never a fictional world-position target
    c.mode='hold';assert step(c)==(0,0,None) and c.command_yaw_rate==0
    c.mode='circle';assert step(c)==(0,0,None) and c.command_yaw_rate==0


@pytest.mark.parametrize('change',[
    lambda s:s.update(paused=True),lambda s:s.update(error='camera failed'),
    lambda s:s.update(onboard_provenance='untrusted'),lambda s:s['detector'].update(engine='ort'),
    lambda s:s['stats']['capabilities'].update(mount='fixed'),lambda s:s['stats']['capabilities'].update(camera='oak'),
    lambda s:s['onboard'].update(valid=False),lambda s:s['onboard'].update(transmit=True),
    lambda s:s['onboard'].update(frame_seq=0),lambda s:s['onboard'].update(frame_epoch='old'),
    lambda s:s['onboard'].update(instance='old'),lambda s:s['onboard'].update(target_id=9),
    lambda s:s['onboard'].update(selection_gen=0),
    lambda s:s['onboard'].update(target_id=True),lambda s:s['onboard'].update(forward_vel=math.nan),
    lambda s:s['onboard'].update(forward_vel=.81),lambda s:s['onboard'].update(forward_vel=-.41),
    lambda s:s['onboard'].update(yaw_rate=.51),lambda s:s['onboard'].update(vertical_vel=.1),
    lambda s:s['onboard'].update(range_source='stereo'),lambda s:s['onboard'].update(range_m=2),
    lambda s:s['tracks'][0].update(coasting=True),lambda s:s['tracks'][0].update(label='chair'),
    lambda s:s['stats'].update(identity=dict(confirmed=True,target=7)),
    lambda s:s.update(age_ms=-1),lambda s:s.update(age_ms=math.nan),lambda s:s.update(age_ms=True),
])
def test_bad_command_never_moves(change):
    c=core();s=snap(2);change(s)
    assert step(c,s)==(0,0,None) and c.command_yaw_rate==0


def test_frozen_and_replayed_observations_latch_reselection():
    c=core();assert step(c)[:2]!=(0,0)
    # Fresh HTTP replies with the same frame cannot renew the command.
    for n in range(1,6):step(c,snap(),n*.1)
    assert c.requires_reselection and step(c,snap(2),.6)==(0,0,None)
    c.select(7);c.mode='follow';assert step(c,snap(3,c.selection_generation),.65)[:2]!=(0,0)
    assert step(c,snap(2),.7)==(0,0,None) and c.requires_reselection


@pytest.mark.parametrize('q',[[0,0,0,0],[math.nan,0,0,0],[.9,0,0,0],[.9,.435889894,0,0]])
def test_missing_bad_or_tilted_attitude_holds(q):
    c=core();assert step(c,q=q)==(0,0,None) and c.command_yaw_rate==0


def test_boundary_and_speed_limit():
    c=core();c.speed=.2;assert step(c)[0]==.2
    assert step(c,pos=[10,0,-1.5])==(0,0,None)
    assert c.command_yaw_rate==0


def test_detector_restart_requires_reselection():
    c=core();s=snap(2);s['stats']['instance']='new';s['onboard']['instance']='new'
    assert step(c,s)==(0,0,None) and c.requires_reselection


@pytest.mark.parametrize('change',[
    lambda s:s['onboard'].update(target_id=9),
    lambda s:s['stats'].update(identity=dict(confirmed=False,target=7,state='checking',
        frame_seq=2,frame_epoch='cam',instance='pi-rpc')),
])
def test_identity_contradiction_requires_explicit_reselection(change):
    c=core();assert step(c)[:2]!=(0,0)
    bad=snap(2);change(bad)
    assert step(c,bad,.1)==(0,0,None) and c.command_yaw_rate==0
    assert c.requires_reselection
    assert step(c,snap(3),.2)==(0,0,None) and c.command_yaw_rate==0
    c.select(7)
    assert c.mode=='hold'
    assert step(c,snap(4,c.selection_generation),.25)==(0,0,None)
    c.mode='follow'
    assert step(c,snap(5,c.selection_generation),.3)[:2]!=(0,0)
