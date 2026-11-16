import copy
import math
import pytest
from sim_onboard import SimOnboard


def request(seq=1):
    return dict(request_id='infer',frame=dict(seq=seq,epoch='gazebo',selection_gen=1),observation=dict(frame_w=1280,frame_h=720,age_ms=50,
        identity=None,range_source='box_height',stereo=None,
        target=dict(id=7,x=600,y=200,w=150,h=200,conf=.9,coasting=False)))


def row():return dict(x=600,y=200,w=150,h=200,confidence=.9,label='person')


def test_inference_binding_once_only_and_ramp_reset():
    now=[1.];s=SimOnboard('pi',clock=lambda:now[0])
    for i in range(10):
        s.remember('infer',[row()]);o=s.compute(request(i));now[0]+=.05
    assert o['forward_vel']>0 and o['transmit'] is False and o['instance']=='pi'
    with pytest.raises(ValueError):s.compute(request())
    s.remember('infer',[row()]);assert s.compute(request(11))['forward_vel']==0
    s.remember('infer',[row()]);r=request(12);r['observation']['target']['id']=8
    assert s.compute(r)['forward_vel']==0


@pytest.mark.parametrize('change',[
    lambda r:r.update(request_id='old'),lambda r:r['frame'].update(seq=True),lambda r:r['frame'].update(epoch=''),
    lambda r:r['observation'].update(range_source='stereo'),lambda r:r['observation'].update(identity=True),
    lambda r:r['observation'].update(age_ms=math.nan),lambda r:r['observation'].update(age_ms=-1),
    lambda r:r['observation']['target'].update(x=10),lambda r:r['observation']['target'].update(x=math.inf),
    lambda r:r['observation']['target'].update(coasting=True),
])
def test_invalid_request_cannot_reuse_inference(change):
    s=SimOnboard('pi');s.remember('infer',[row()]);r=request();change(r)
    with pytest.raises(ValueError):s.compute(r)
    with pytest.raises(ValueError):s.compute(request())


def test_old_request_and_stale_age():
    now=[0.];s=SimOnboard('pi',clock=lambda:now[0]);s.remember('infer',[row()]);now[0]=.41
    with pytest.raises(ValueError):s.compute(request())
    s.remember('infer',[row()]);r=request();r['observation']['age_ms']=401
    assert not s.compute(r)['valid']


def test_no_selected_person_zeroes():
    s=SimOnboard('pi');s.remember('infer',[row()]);r=request();r['observation']['target']=None
    o=s.compute(r);assert not o['valid'] and o['forward_vel']==o['yaw_rate']==0


def test_new_generation_resets_same_person_even_if_clear_was_skipped():
    now=[0.];s=SimOnboard('pi',clock=lambda:now[0])
    for i in range(8):
        s.remember('infer',[row()]);o=s.compute(request(i));now[0]+=.05
    assert o['forward_vel']>0
    r=request(9);r['frame']['selection_gen']=3
    s.remember('infer',[row()]);assert s.compute(r)['forward_vel']==0


def test_missing_selected_person_is_distinct_from_no_selection():
    s=SimOnboard('pi');s.remember('infer',[]);r=request();r['observation'].update(target=None,selected_id=7)
    o=s.compute(r);assert not o['valid'] and o['reason']=='selected person not seen in this frame'
