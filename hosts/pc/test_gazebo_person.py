"""The Gazebo figure that mirrors the real person: world edit and placement rule (pure parts)."""
import xml.etree.ElementTree as ET
from pathlib import Path
import pytest
from gazebo_person import external_person_world,world_pose,MODEL,HIDDEN,PERSON_SDF,Steady

LAB=(Path(__file__).with_name('gazebo_lab.sdf')).read_text().replace('@ACTOR_MESH@','/tmp/walk.dae')

def state(**change):
    s=dict(source_kind='fixed_stereo',lock_state='LOCKED',requires_reselection=False,perception_age_ms=150,target_id=7,
        target=[2.4,-0.6],fixed_stereo=dict(status='ok',target_id=7))
    s.update(change);return s

def test_world_replaces_the_scripted_walker_with_one_static_figure_and_keeps_the_rest():
    before=ET.fromstring(LAB).find('world');after=ET.fromstring(external_person_world(LAB)).find('world')
    assert len(before.findall('actor'))==1 and after.findall('actor')==[]
    names=lambda w:[m.get('name') for m in w.findall('model')]
    assert names(after)==[n for n in names(before)[:names(before).index('occluder')+1]]+[MODEL]+names(before)[names(before).index('occluder')+1:]
    figure=next(m for m in after.findall('model') if m.get('name')==MODEL)
    assert figure.find('static').text=='true' and figure.find('link').find('collision') is None      # nothing to collide with
    assert [float(v) for v in figure.find('pose').text.split()][:3]==list(HIDDEN)                    # starts out of sight
    assert after.find(".//model[@name='garuda_x500']") is not None

def test_world_edit_refuses_an_unexpected_world():
    with pytest.raises(ValueError):external_person_world(external_person_world(LAB))               # no actor left
    with pytest.raises(ValueError):external_person_world('<sdf><world name="w"/></sdf>')

def test_placement_maps_local_north_east_to_world_y_x_on_the_ground():
    assert world_pose(state())==((-0.6,2.4,0.0),'')
    pose,_=world_pose(state(),origin_xy=(10.,-3.));assert pose==pytest.approx((9.4,-0.6,0.0))

@pytest.mark.parametrize('change',[dict(source_kind='pi'),dict(source_kind='gazebo'),dict(lock_state='LOST'),dict(lock_state='STALE'),dict(requires_reselection=True),
    dict(perception_age_ms=700),dict(perception_age_ms=None),dict(perception_age_ms=-1),dict(perception_age_ms=float('nan')),
    dict(fixed_stereo=None),dict(fixed_stereo=dict(status='invalid',target_id=7)),dict(fixed_stereo=dict(status='ok',target_id=8)),
    dict(target=None),dict(target=[1.0]),dict(target=[float('inf'),0.]),dict(target=['1','2'])])
def test_without_a_current_measurement_of_the_selected_person_the_figure_is_not_shown(change):
    pose,reason=world_pose(state(**change));assert pose is None and reason

def test_bad_state_objects_are_not_shown():
    for bad in (None,[],'x',{}):assert world_pose(bad)[0] is None

def test_figure_sdf_is_well_formed():
    m=ET.fromstring(PERSON_SDF);assert m.get('name')==MODEL and len(m.find('link').findall('visual'))==3

# ---- display steadiness (owner: "why does the man in gazebo flicker") ----------------------
def run(steady,readings,dt=1/15):
    out=[]
    for n,pose in enumerate(readings):
        want=steady.step(pose,n*dt)
        if want is not None:steady.confirm(want)
        out.append(want)
    return out

def test_a_short_gap_does_not_hide_the_figure_but_a_long_one_does():
    s=Steady(hold_s=.7);p=(1.,2.,0.)
    out=run(s,[p]+[None]*8+[p])                       # 8 frames = 0.53 s without a reading
    assert out[0]==p and HIDDEN not in out
    out=run(Steady(hold_s=.7),[p]+[None]*14)          # 0.93 s
    assert out.count(HIDDEN)==1 and out[-1] is None   # hidden once, not re-sent every frame

def test_noise_is_smoothed_and_tiny_moves_are_not_sent():
    s=Steady(alpha=.35,deadband_m=.02)
    out=run(s,[(1.,2.,0.)]+[(1.+(.01 if n%2 else -.01),2.,0.) for n in range(30)])
    assert out[0]==(1.,2.,0.) and all(o is None for o in out[1:])        # 1 cm jitter never moves the figure
    s=Steady(alpha=.35,deadband_m=.02);run(s,[(0.,0.,0.)])
    path=[w for w in run(s,[(1.,0.,0.)]*25) if w is not None]
    xs=[w[0] for w in path];assert xs==sorted(xs) and .30<xs[0]<.40 and xs[-1]>.97   # eases towards a real move, no overshoot

def test_reappearing_after_being_hidden_starts_at_the_new_place():
    s=Steady(hold_s=.2);run(s,[(0.,0.,0.)]+[None]*10)
    assert s.sent==HIDDEN and s.step((5.,5.,0.),10.)==(5.,5.,0.)           # no slide across the world from the old spot


def test_explicit_mono_schematic_target_is_visualized_without_claiming_stereo():
    s=dict(source_kind='mono_estimate',lock_state='LOCKED',requires_reselection=False,
           perception_age_ms=50,target_id=7,target=[2.,.5],
           mono_estimate=dict(accepted=True,simulation_only=True,method='box-size-schematic-v1'))
    assert world_pose(s)==((.5,2.,0.),'')
    for field in ('accepted','simulation_only','method'):
        bad={**s,'mono_estimate':dict(s['mono_estimate'])};bad['mono_estimate'].pop(field)
        assert world_pose(bad)[0] is None
    assert world_pose({**s,'target':None})[0] is None
    assert world_pose({**s,'perception_age_ms':701})[0] is None

def test_onboard_display_is_independent_of_follow_grant_but_bound_to_fresh_frame():
    s=dict(source_kind='pi_onboard',telemetry_fresh=True,perception_age_ms=50,
           frame=dict(epoch='cam',seq=12),lock_state='RESELECT',requires_reselection=True,
           onboard_visual=dict(valid=True,simulation_only=True,method='onboard-stereo-display-v1',
                               frame_epoch='cam',frame_seq=12,target_id=7,target=[3.,-1.]))
    assert world_pose(s)[0]==(-1.,3.,0.)
    for change in ({'valid':False},{'simulation_only':False},{'frame_seq':11},{'target':[float('nan'),0.]},{'method':'unknown'}):
        bad={**s,'onboard_visual':{**s['onboard_visual'],**change}}
        assert world_pose(bad)[0] is None
    assert world_pose({**s,'telemetry_fresh':False})[0] is None
    assert world_pose({**s,'perception_age_ms':401})[0] is None
