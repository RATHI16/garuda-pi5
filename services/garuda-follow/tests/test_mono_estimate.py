import math
import pytest
from sitl_demo_core import DemoControl
from mono_estimate import provenance


def snap(seq=1):
    return dict(kind='mono_estimate',age_ms=50,stats=dict(instance='detector',capabilities=dict(distance_source='mono_estimate',camera='cm3')),
                frame=dict(epoch='cm3-1',seq=seq,width=1280,height=720),
                tracks=[dict(id=7,label='person',conf=.9,x=400,y=100,w=200,h=400,coasting=False)])


def controller(accept=True):
    c=DemoControl();c.accept_mono=accept;c.receive(snap(),0);c.select(7);c.mode='follow';return c


def step(c,s=None,now=.05):
    if s is not None:c.receive(s,now)
    return c.velocity([0.,0.,-1.5],now,.05,[1.,0.,0.,0.])


def test_explicit_opt_in_and_schematic_provenance():
    c=controller(False);assert step(c,snap(2))[:2]==(0,0)
    assert '--accept-mono-estimate-in-simulation' in c.reason
    c=controller();assert step(c,snap(2))[:2]!=(0,0)
    assert c.target is not None and 'schematic' in c.reason
    assert provenance(True)['measurement_qualified'] is False


@pytest.mark.parametrize('change',[
    lambda s:s['stats']['capabilities'].update(mount='vehicle'),
    lambda s:s['stats'].pop('capabilities'),
    lambda s:s['stats']['capabilities'].update(distance_source='stereo'),
    lambda s:s['stats']['capabilities'].update(camera='oak'),
    lambda s:s['stats']['capabilities'].update(camera='unknown'),
    lambda s:s.update(stereo={}),
    lambda s:s['frame'].update(width=math.nan),
    lambda s:s['tracks'][0].update(h=math.inf),
    lambda s:s['tracks'][0].update(x=-1),
    lambda s:s['stats'].update(instance=None),
])
def test_invalid_or_stereo_input_never_moves(change):
    c=controller();s=snap(2);change(s)
    assert step(c,s)[:2]==(0,0) and c.target is None


def test_stale_restart_and_missing_person_require_reselection():
    for change in (lambda s:s.update(age_ms=800), lambda s:s['frame'].update(epoch='restart'),lambda s:s['stats'].update(instance='restart')):
        c=controller();s=snap(2);change(s)
        assert step(c,s)[:2]==(0,0) and c.requires_reselection
    c=controller();step(c,snap(2))
    s=snap(3);s['tracks']=[];assert step(c,s,.2)[:2]==(0,0)
    s['frame']['seq']=4;step(c,s,.7)
    assert c.requires_reselection
    assert step(c,snap(5),.75)[:2]==(0,0)


def test_mono_never_uses_unrelated_model_distance_for_world_localization():
    c=controller();s=snap(2);s['distance']=dict(valid=True,est_m=999)
    step(c,s);first=c.target
    s=snap(3);s['distance']=dict(valid=True,est_m=.1);step(c,s,.1)
    assert c.target==first


@pytest.mark.parametrize('change',[
    lambda d:d.update(frame_seq=1),
    lambda d:d.update(frame_epoch='old'),
    lambda d:d.update(instance='old'),
    lambda d:d.update(target=8),
    lambda d:d.update(confirmed=False),
])
def test_present_identity_must_confirm_this_frame_and_person(change):
    c=controller();s=snap(2)
    d=dict(target=7,confirmed=True,instance='detector',frame_epoch='cm3-1',frame_seq=2)
    s['stats']['identity']=d;change(d)
    assert step(c,s)[:2]==(0,0) and c.target is None


def test_confirmed_mono_identity_can_follow_but_never_implicitly_rebinds():
    c=controller();c.reacquire=True;s=snap(2)
    s['stats']['identity']=dict(target=7,confirmed=True,instance='detector',frame_epoch='cm3-1',frame_seq=2)
    assert step(c,s)[:2]!=(0,0)
    s=snap(3);s['tracks'][0]['id']=9
    s['stats']['identity']=dict(target=9,rebound_from=7,confirmed=True,instance='detector',frame_epoch='cm3-1',frame_seq=3)
    assert step(c,s,.1)[:2]==(0,0) and c.target_id==7
