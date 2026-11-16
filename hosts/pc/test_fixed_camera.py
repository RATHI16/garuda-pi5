import copy
import pytest
from fixed_camera import FixedCamera

@pytest.fixture
def case():
    c={'mount_verified':True,'evidence_ref':'synthetic-test-only','calibration_id':'c','position_m':[0,0,1], 'world_from_optical':[[0,0,1],[-1,0,0],[0,-1,0]]}
    s={'age_ms':10,'frame':{'epoch':'e','seq':1,'width':640,'height':400,'capture_pair':{'epoch':'raw','rgb_seq':8}},'lock':{'id':1},'tracks':[{'id':1,'label':'person','conf':.9,'coasting':False}], 'distance':{'est_m':3},'stereo':{'measurement_qualified':True,'preview_only':False,'status':'ok','z_m':3,'pair':{'epoch':'raw','rgb_seq':8},'calibration_id':'c','depth_reference_frame':'rgb_optical','roi':[300,180,340,220],'geometry':{'verified':True,'reference_frame':'rgb_optical','distortion':'none','epoch':'raw','calibration_id':'c','width':640,'height':400,'fx':400,'fy':400,'cx':320,'cy':200}}}
    core=FixedCamera(c);core.select(s,1,'e')
    return core,s,{'armed':False,'landed':True,'telemetry_fresh':True}

def test_projection(case):
    c,s,sim=case
    assert c.evaluate(s,sim,0)['position_m']==[3,0,1]

@pytest.mark.parametrize('field,value', [('measurement_qualified',False),('preview_only',True),('depth_reference_frame','left_optical'),('z_m',float('nan')),('status','ambiguous')])
def test_depth_gates(case,field,value):
    c,s,sim=case;s['stereo'][field]=value
    assert c.evaluate(s,sim,0)['position_m'] is None

def test_no_fallback(case):
    c,s,sim=case;s.pop('stereo')
    assert c.evaluate(s,sim,0)['position_m'] is None

def test_wrong_frame(case):
    c,s,sim=case;s['frame']['capture_pair']['rgb_seq']=9
    assert c.evaluate(s,sim,0)['position_m'] is None

@pytest.mark.parametrize('field,value',[('armed',True),('landed',False),('telemetry_fresh',False)])
def test_ground_interlock(case,field,value):
    c,s,sim=case;sim[field]=value
    assert c.evaluate(s,sim,0)['position_m'] is None

def test_gap_requires_explicit_selection(case):
    c,s,sim=case;assert c.evaluate(s,sim,0)['status']=='ok'
    s['frame']['seq']=2
    assert c.evaluate(s,sim,1)['requires_reselection']
    assert c.evaluate(s,sim,1.1)['position_m'] is None
    c.select(s,1,'e');assert c.evaluate(s,sim,1.2)['status']=='ok'

def test_loss_latches(case):
    c,s,sim=case;s['tracks'][0]['coasting']=True;c.evaluate(s,sim,0)
    s['tracks'][0]['coasting']=False
    assert c.evaluate(s,sim,.1)['requires_reselection']

@pytest.mark.parametrize('conf',[float('nan'),float('inf'),-1,2])
def test_invalid_confidence(case,conf):
    c,s,sim=case;s['tracks'][0]['conf']=conf
    assert c.evaluate(s,sim,0)['position_m'] is None
    with pytest.raises(ValueError):c.select(s,1,'e')

def test_mount_unverified(case):
    c,s,sim=case;c.calibration['mount_verified']=False
    assert c.evaluate(s,sim,0)['position_m'] is None


def test_bridge_stale_snapshot_removes_position():
    from fixed_camera_bridge import Bridge
    b=Bridge('http://localhost:1','http://localhost:2',{})
    b.result={'status':'ok','position_m':[1,2,3]}
    assert b.snapshot()['position_m'] is None



@pytest.mark.parametrize('backend,present',[('pi-sgbm',True),('mono',False)])
def test_preview_route_only_exposes_host_stereo(monkeypatch,backend,present):
    import io,json,threading,urllib.request
    from http.server import ThreadingHTTPServer
    from fixed_camera_bridge import Bridge,handler
    data={'rgb':'rgb','depth':'depth','depth_present':True,'depth_backend':backend}
    monkeypatch.setattr('fixed_camera_bridge.urlopen',lambda *a,**k:io.BytesIO(json.dumps(data).encode()))
    b=Bridge('http://camera:8098','',{})
    class Reader:
        def snapshot(self,*args):return dict(data)
    b.depth_reader=Reader()
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(b))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/api/preview') as r:got=json.load(r)
        assert got['depth_present'] is present
        assert got['depth']==('depth' if present else None)
    finally:server.shutdown();server.server_close();thread.join()


def test_selection_ui_stays_on_stereo_page():
    from fixed_camera_bridge import PAGE
    assert 'Choose a person in camera controls' not in PAGE
    assert 'choose.href' not in PAGE
    assert "fetch('/api/choose'" in PAGE
    assert "rect.onclick=()=>choose(p)" in PAGE
    assert "'/stream?clean=1'" in PAGE


def test_choose_rejects_wrong_epoch_before_contacting_pi(monkeypatch):
    import time
    from fixed_camera_bridge import Bridge
    b=Bridge('http://camera:8098','',{})
    b.source={'age_ms':1,'frame':{'epoch':'new'}};b.at=time.monotonic()
    monkeypatch.setattr('fixed_camera_bridge.urlopen',lambda *a,**k:pytest.fail('Must not contact Pi'))
    with pytest.raises(ValueError,match='Image changed'):b.choose(1,'old')


def test_stereo_cache_expires_without_new_pair():
    from stereo_preview import StereoPreview
    import time
    reader=StereoPreview('http://unused')
    reader.value={'depth_present':True,'depth':'old'};reader.at=time.monotonic()-2
    assert reader.snapshot()['depth_present'] is False
    assert 'depth' not in reader.snapshot()


def test_object_detections_are_preserved_for_click_selection():
    from fixed_camera_bridge import Bridge
    import time
    b=Bridge('','',{});b.at=time.monotonic()
    b.source={'tracks':[{'id':3,'label':'chair','conf':.8,'coasting':False},{'id':4,'label':'person','conf':.9,'coasting':True}]}
    assert [p['label'] for p in b.snapshot()['people']]==['chair']
