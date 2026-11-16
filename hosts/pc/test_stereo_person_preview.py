import time
import numpy as np
from fixed_camera_bridge import Bridge
from stereo_person_preview import torso_stat
from stereo_preview import StereoPreview

def test_torso_stat_ignores_zero_and_reports_unqualified():
    d=np.full((100,100),2000,np.uint16);d[::2]=0
    r=torso_stat(d,[0,0,100,100])
    assert r['z_m']==2 and r['measurement_qualified'] is False
    assert r['pixels']>30

def test_no_pixels_no_number():
    assert 'z_m' not in torso_stat(np.zeros((100,100),np.uint16),[0,0,100,100])

def fixture_bridge():
    b=Bridge('','',{});b.at=time.monotonic()
    s={'age_ms':10,'stats':{'instance':'proc'},'frame':{'epoch':'oak-rgb:raw:0','capture_host_ns':10000,'width':100,'height':100},'lock':{'id':1},'tracks':[{'id':1,'label':'person','coasting':False,'x':0,'y':0,'w':100,'h':100}]}
    b.source=s;b.history.append((b.at,s));return b

def test_frame_matching_not_latest_depth():
    b=fixture_bridge();d=np.full((100,100),2000,np.uint16)
    meta={'epoch':'raw','rgb':{'capture_host_ns':10000,'width':100,'height':100},'pair_skew_ns':20000}
    assert b.person_depth(meta,d)['z_m']==2
    meta['rgb']['capture_host_ns']=20000
    assert 'z_m' not in b.person_depth(meta,d)
    meta['rgb']['capture_host_ns']=10000;meta['epoch']='other'
    assert 'z_m' not in b.person_depth(meta,d)

def test_distance_hold_expires_and_cannot_change_target():
    r=StereoPreview('');r.at=time.monotonic();r.value={'depth_present':True}
    r.distance={'selection_key':['p','e',1],'age_ms':10,'z_m':2};r.distance_at=time.monotonic()
    assert r.snapshot(['p','e',1])['person_depth']['z_m']==2
    assert 'z_m' not in r.snapshot(['p','e',2])['person_depth']
    r.distance_at-=1
    assert 'z_m' not in r.snapshot(['p','e',1])['person_depth']
