"""Contract tests for garuda.fixed-stereo-observation.1 (astra-0061)."""
import copy,json,math
import pytest
from fixed_stereo_observation import decode,SCHEMA,SOURCE

GEOMETRY=dict(schema_version=1,epoch='E',calibration_id='cal',width=1280,height=720,fx=800.,fy=790.,cx=640.,cy=360.,distortion='none',
    source='host-opencv-factory-calibration',reference_frame='unverified',verified=False)
def snapshot():
    return {'v':2,'age_ms':140,'stats':{'instance':'proc','fps':15.0},
        'frame':{'capture_host_ns':5_000_000_000,'width':1280,'height':720,'seq':41,'source':'oak-rgb','epoch':'oak-rgb:E:0','capture_pair':{'epoch':'E','rgb_seq':900}},
        'lock':{'id':7,'class':'person','coasting':False},
        'tracks':[{'id':7,'label':'person','conf':.9,'x':700.,'y':100.,'w':240.,'h':560.,'coasting':False},{'id':8,'label':'chair','conf':.8,'x':0.,'y':0.,'w':10.,'h':10.,'coasting':False}],
        'stereo':{'status':'ok','reason':'ok','z_m':2.0,'coverage':.7,'support':.9,'roi':[772,212,868,408],'roi_source':'box',
            'pair':{'epoch':'E','rgb_seq':900,'pair_id':900},'depth_reference_frame':'unverified','calibration_id':'cal','pair_skew_ns':100000,
            'geometry':dict(GEOMETRY),'measurement_qualified':False,'preview_only':True,'estimator_config':'abc123','source':'host-box-stereo'}}
KEYS={'schema','source','source_instance','frame_epoch','frame_seq','target_id','target_class','coasting','confidence','camera_capture_host_ns','age_ms',
    'pair','calibration_id','estimator_config','roi','status','reason','optical_point_m','measurement_qualified','preview_only'}

def test_valid_snapshot_gives_the_optical_point_and_full_provenance():
    o=decode(snapshot())
    assert set(o)==KEYS and o['schema']==SCHEMA=='garuda.fixed-stereo-observation.1' and o['source']==SOURCE=='fixed_stereo'
    assert (o['source_instance'],o['frame_epoch'],o['frame_seq'])==('proc','oak-rgb:E:0',41)
    assert (o['target_id'],o['target_class'],o['coasting'],o['confidence'])==(7,'person',False,.9)
    assert o['camera_capture_host_ns']==5_000_000_000 and o['age_ms']==140
    assert o['pair']=={'epoch':'E','rgb_seq':900} and o['calibration_id']=='cal' and o['estimator_config']=='abc123' and o['roi']==[772,212,868,408]
    assert o['status']=='ok' and o['reason']=='ok'
    # Region centre (820, 310) at 2 m: right (820-640)*2/800, down (310-360)*2/790, forward 2.
    assert o['optical_point_m']==pytest.approx([0.45,-50*2/790,2.0])
    json.dumps(o,allow_nan=False)

def test_qualification_flags_are_copied_and_never_upgraded():
    o=decode(snapshot());assert o['measurement_qualified'] is False and o['preview_only'] is True
    s=snapshot();s['stereo'].update(measurement_qualified=True,preview_only=False);o=decode(s)
    assert o['measurement_qualified'] is True and o['preview_only'] is False
    for q,p in [(1,0),('true','false'),(None,None)]:
        s=snapshot();s['stereo'].update(measurement_qualified=q,preview_only=p);o=decode(s)
        assert o['measurement_qualified'] is False and o['preview_only'] is True
    s=snapshot();del s['stereo']['measurement_qualified'];del s['stereo']['preview_only'];o=decode(s)
    assert o['measurement_qualified'] is False and o['preview_only'] is True

def test_the_producers_refusal_is_passed_through_without_a_point():
    for status,reason in [('invalid','cluster_too_spread'),('ambiguous','competing_depth_clusters'),('unavailable','frame_not_in_history'),('unavailable','stale_state')]:
        s=snapshot();s['stereo'].update(status=status,reason=reason,z_m=None);o=decode(s)
        assert (o['status'],o['reason'],o['optical_point_m'])==(status,reason,None) and o['target_id']==7 and o['pair']=={'epoch':'E','rgb_seq':900}

def change(path,value):
    def apply(s):
        d=s
        for k in path[:-1]:d=d[k]
        if value is DELETE:d.pop(path[-1],None)
        else:d[path[-1]]=value
    return apply
DELETE=object()
CASES=[
 (change(['stats','instance'],DELETE),'identity_or_time_missing'),(change(['frame','epoch'],''),'identity_or_time_missing'),
 (change(['frame','seq'],41.0),'identity_or_time_missing'),(change(['frame','capture_host_ns'],5e9),'identity_or_time_missing'),
 (change(['age_ms'],None),'identity_or_time_missing'),(change(['age_ms'],-1),'identity_or_time_missing'),(change(['age_ms'],float('nan')),'identity_or_time_missing'),
 (change(['frame','width'],0),'image_size_invalid'),(change(['stereo'],DELETE),'no_stereo_block'),
 (change(['lock','id'],None),'no_selected_target'),(change(['lock','id'],99),'selected_target_not_in_frame'),(change(['lock','id'],8),'selected_target_not_a_person'),
 (change(['tracks',0,'coasting'],True),'selected_target_coasting'),(change(['lock','coasting'],True),'selected_target_coasting'),
 (change(['tracks',0,'conf'],.49),'selected_target_confidence_low'),(change(['tracks',0,'conf'],None),'selected_target_confidence_low'),
 (change(['stereo','pair'],None),'stereo_pair_identity_missing'),(change(['stereo','pair','rgb_seq'],'900'),'stereo_pair_identity_missing'),
 (change(['frame','capture_pair'],DELETE),'depth_not_from_this_image'),(change(['frame','capture_pair','rgb_seq'],901),'depth_not_from_this_image'),
 (change(['frame','capture_pair','epoch'],'F'),'depth_not_from_this_image'),(change(['stereo','status'],None),'stereo_status_missing'),
 (change(['stereo','z_m'],0.3),'depth_out_of_range'),(change(['stereo','z_m'],30.0),'depth_out_of_range'),(change(['stereo','z_m'],float('inf')),'depth_out_of_range'),
 (change(['stereo','z_m'],None),'depth_out_of_range'),(change(['stereo','z_m'],True),'depth_out_of_range'),
 (change(['stereo','geometry'],None),'geometry_missing'),(change(['stereo','geometry','distortion'],'rational'),'geometry_missing'),
 (change(['stereo','geometry','calibration_id'],'other'),'geometry_identity_mismatch'),(change(['stereo','geometry','epoch'],'F'),'geometry_identity_mismatch'),
 (change(['stereo','calibration_id'],None),'geometry_identity_mismatch'),(change(['stereo','geometry','width'],640),'geometry_size_mismatch'),
 (change(['stereo','geometry','fx'],0),'geometry_intrinsics_invalid'),(change(['stereo','geometry','cx'],1280.),'geometry_intrinsics_invalid'),
 (change(['stereo','geometry','fy'],float('nan')),'geometry_intrinsics_invalid'),
 (change(['stereo','roi'],None),'measurement_region_missing'),(change(['stereo','roi'],[772,212,868]),'measurement_region_missing'),
 (change(['stereo','roi'],[868,212,772,408]),'measurement_region_invalid'),(change(['stereo','roi'],[772,212,1300,408]),'measurement_region_invalid'),
]
@pytest.mark.parametrize('mutate,reason',CASES)
def test_every_broken_precondition_gives_no_point_and_a_named_reason(mutate,reason):
    s=snapshot();mutate(s);o=decode(s)
    assert set(o)==KEYS and o['status']=='unavailable' and o['reason']=='adapter:'+reason and o['optical_point_m'] is None
    assert o['measurement_qualified'] is False                       # nothing here may raise the qualification
    json.dumps(o,allow_nan=False)

@pytest.mark.parametrize('bad',[None,[],'x',5,{},{'stats':{},'frame':{},'lock':{}},{'stats':1,'frame':2,'lock':3,'tracks':4},
    {'stats':{'instance':'p'},'frame':{'epoch':'e','seq':1,'capture_host_ns':5,'width':1280,'height':720},'lock':{},'tracks':'none','age_ms':1}])
def test_malformed_input_never_raises(bad):
    o=decode(bad);assert set(o)==KEYS and o['status']=='unavailable' and o['optical_point_m'] is None and o['reason'].startswith('adapter:')

def test_stateless_no_carry_over_and_input_untouched():
    good=snapshot();frozen=copy.deepcopy(good);first=decode(good);assert good==frozen and first['status']=='ok'
    lost=snapshot();lost['stereo'].update(status='invalid',reason='no_valid_depth',z_m=None)
    assert decode(lost)['optical_point_m'] is None                    # the previous value is not reused
    assert decode(good)==first                                        # same input, same output

def test_matches_the_existing_fixed_camera_projection():
    # fixed_camera.py is the reference for the optical point; it only runs for qualified depth,
    # so the same snapshot is given to it with the gates it needs satisfied.
    from fixed_camera import FixedCamera
    s=snapshot();s['stereo'].update(measurement_qualified=True,preview_only=False,depth_reference_frame='rgb_optical')
    s['stereo']['geometry'].update(verified=True,reference_frame='rgb_optical')
    cam=FixedCamera(dict(position_m=[0.,0.,0.],world_from_optical=[[1.,0,0],[0,1.,0],[0,0,1.]],calibration_id='cal',mount_verified=True,evidence_ref='test'))
    cam.select(s,7,'oak-rgb:E:0')
    ref=cam.evaluate(s,dict(armed=False,landed=True,telemetry_fresh=True),now=1.0)
    assert ref['status']=='ok' and decode(s)['optical_point_m']==pytest.approx(ref['optical_point_m'])
