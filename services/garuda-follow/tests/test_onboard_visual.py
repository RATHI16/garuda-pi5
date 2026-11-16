import math
import pytest
from onboard_visual import project
from .test_live_onboard import snapshot

def scene():
    s=snapshot(camera='oak');s['onboard'].update(range_m=2.,range_source='stereo')
    s['frame'].update(width=1280,height=720)
    s['stereo']=dict(status='ok',z_m=2.,roi=[600,300,680,500],calibration_id='cal',pair={'epoch':'e'},
        geometry=dict(width=1280,height=720,fx=1000,cx=640,epoch='e',calibration_id='cal'))
    return s

def test_display_projects_relative_to_simulator_without_a_follow_request():
    s=scene();s['transmit']['request']='off'
    a=project(s,50,50,[10,20,-2],[1,0,0,0],True)
    assert a['valid'] and a['target']==[12,20] and a['simulation_only']
    b=project(s,50,50,[10,20,-2],[math.sqrt(.5),0,0,math.sqrt(.5)],True)
    assert b['target']==pytest.approx([10,22])

def test_missing_depth_stale_pose_and_identity_never_invent_person_position():
    for change in ('depth','pose','age','identity','geometry'):
        s=scene();fresh=True;age=50
        if change=='depth':s['stereo']['status']='invalid'
        if change=='pose':fresh=False
        if change=='age':age=500
        if change=='identity':s['onboard']['target_id']=99
        if change=='geometry':s['stereo']['geometry']['calibration_id']='other'
        assert not project(s,age,50,[0,0,0],[1,0,0,0],fresh)['valid']
