"""Offline checks for pair-cursor reading, association retry and bridge diagnostics."""
import time
from types import SimpleNamespace
import numpy as np
import pytest
from fixed_camera_bridge import Bridge,local_origin
from stereo_preview import StereoPreview

def pair(pid,epoch='raw',stamp=10000,backend='pi-sgbm'):
    meta={'epoch':epoch,'pair_id':pid,'depth_backend':backend,'rgb':{'capture_host_ns':stamp},'pair_skew_ns':0}
    return SimpleNamespace(meta=meta,depth_mm=np.full((100,100),2000,np.uint16),key=(epoch,pid))
render=lambda depth:('image',1.0)

def test_cursor_requests_only_newer_pairs():
    r=StereoPreview('')
    assert r.query()=='/pair-preview?depth=1'
    r.accept(pair(7),render)
    assert r.query()=='/pair-preview?depth=1&after=7&epoch=raw'
    r.accept(pair(0,'next'),render)
    assert r.query()=='/pair-preview?depth=1&after=0&epoch=next'

def test_repeat_pair_is_not_counted_or_rendered_again():
    r=StereoPreview('');calls=[]
    def counting(depth):calls.append(1);return render(depth)
    assert r.accept(pair(1),counting) and not r.accept(pair(1),counting)
    assert len(calls)==1 and r.counts['pairs']==1 and r.counts['duplicate_pair']==1
    assert len(r.times)==1

def test_late_detection_is_matched_against_cached_pair_without_refetch():
    answers=[{'reason':'Waiting for stereo paired with a processed detection frame'},{'z_m':2.,'reason':'Median valid torso-region stereo pixels; unvalidated','selection_key':['p','e',1],'age_ms':5}]
    seen=[]
    def person(meta,depth):seen.append(meta['pair_id']);return answers[min(len(seen),2)-1]
    r=StereoPreview('',person);r.accept(pair(3),render)
    assert 'z_m' not in r.snapshot(['p','e',1])['person_depth']
    r.associate()
    assert r.snapshot(['p','e',1])['person_depth']['z_m']==2
    r.associate();r.associate()
    assert seen==[3,3] and r.counts['pair_with_reading']==1

def test_unmatched_pair_is_counted_once_with_its_reason():
    r=StereoPreview('',lambda meta,depth:{'reason':'Select a visible person'})
    r.accept(pair(1),render);r.associate();r.accept(pair(2),render);r.accept(pair(3),render)
    assert r.counts['pair_without_reading: Select a visible person']==2
    assert r.snapshot()['counts']['pairs']==3

def test_old_cached_pair_is_never_associated():
    calls=[]
    r=StereoPreview('',lambda meta,depth:calls.append(1) or {'reason':'waiting'})
    r.accept(pair(1),render);n=len(calls);r.pair_at-=1
    r.associate();r.associate()
    assert len(calls)==n and r.pair_done

@pytest.mark.parametrize('bad',[pair(1,backend='mono'),SimpleNamespace(meta={'depth_backend':'pi-sgbm'},depth_mm=None,key=('raw',1))])
def test_other_backend_or_missing_depth_rejected(bad):
    with pytest.raises(ValueError):StereoPreview('').accept(bad,render)

def test_reader_failure_is_reported_with_cause():
    r=StereoPreview('');r.at=time.monotonic();r.fail(OSError('tunnel closed'))
    s=r.snapshot()
    assert s['depth_present'] is False and s['reader_error']=='OSError: tunnel closed' and s['counts']['read_errors']==1

def test_startup_import_failure_is_visible(monkeypatch):
    import builtins
    real=builtins.__import__
    def fake(name,*a,**k):
        if name=='cv2':raise ImportError('No module named cv2')
        return real(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',fake)
    r=StereoPreview('');r.run()
    assert 'Reader cannot start' in r.snapshot()['reader_error']

def test_no_marker_request_when_nothing_is_shown():
    b=Bridge('','',{})
    assert not b.marker_due(None,0)
    assert b.marker_due([1,2,3],0)
    b.marker_shown=True;b.marker_at=10
    assert not b.marker_due([1,2,3],10.1) and b.marker_due([1,2,3],10.25)
    assert b.marker_due(None,10.1)

def state(seq,n,instance='proc',epoch='e'):
    return {'frame':{'epoch':epoch,'seq':seq},'stats':{'instance':instance,'inferences':n}}
def test_delivery_counts_show_skipped_detector_results():
    b=Bridge('','',{})
    for i,(seq,n) in enumerate([(1,1),(1,1),(2,2),(5,5)]):b.note_poll(state(seq,n),i*.1)
    d=b.delivery()
    assert (d['polls'],d['distinct_results'],d['detector_results_produced'])==(4,3,5)
    assert d['poll_hz']==pytest.approx(10)
    b.note_poll(state(1,1,instance='restarted'),.4)
    d=b.delivery()
    assert (d['distinct_results'],d['detector_results_produced'])==(1,1)

def test_poll_without_identity_is_counted_but_not_as_a_result():
    b=Bridge('','',{});b.note_poll({},0)
    d=b.delivery()
    assert d['polls']==1 and d['distinct_results']==0 and d['detector_results_produced'] is None

@pytest.mark.parametrize('host,origin,ok',[
    ('127.0.0.1:8103','http://127.0.0.1:8103',True),('localhost:8103','http://localhost:8103',True),
    ('evil.example:8103','http://evil.example:8103',False),('127.0.0.1:8103','http://evil.example',False),
    ('127.0.0.1:9999','http://127.0.0.1:9999',False),('127.0.0.1:8103',None,False)])
def test_post_origin_must_be_this_loopback_page(host,origin,ok):
    headers={'Host':host,**({'Origin':origin} if origin else {})}
    assert local_origin(headers,8103) is ok

def test_run_loop_reads_once_then_idles_on_204():
    """Real loopback HTTP and the real pair parser: one transfer per pair, 204 afterwards."""
    import json,sys,threading
    from pathlib import Path
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi5'))
    pytest.importorskip('cv2')
    from oak_pair import encode_pair,pack
    rgb=bytes(1280*720*3);depth=np.full((720,1280),1500,np.uint16)
    body,meta=encode_pair(rgb,depth,pair_id=4,epoch='raw')
    meta.update(depth_backend='pi-sgbm',measurement_qualified=False,preview_only=True)
    body=pack(meta,rgb,depth.astype('<u2').tobytes());paths=[]
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            paths.append(self.path);new='after=4&epoch=raw' not in self.path
            self.send_response(200 if new else 204);self.send_header('Content-Length',str(len(body) if new else 0));self.end_headers()
            if new:self.wfile.write(body)
            else:time.sleep(.05)
    server=ThreadingHTTPServer(('127.0.0.1',0),H);threading.Thread(target=server.serve_forever,daemon=True).start()
    r=StereoPreview('http://127.0.0.1:'+str(server.server_port));t=threading.Thread(target=r.run,daemon=True);t.start()
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and r.counts['no_new_pair']<2:time.sleep(.02)
    finally:r.stop.set();t.join(3);server.shutdown();server.server_close()
    s=r.snapshot()
    assert s['reader_error'] is None and s['depth_present'] and s['sequence']==4
    assert r.counts['pairs']==1 and r.counts['duplicate_pair']==0 and r.counts['no_new_pair']>=2
    assert paths[0]=='/pair-preview?depth=1' and all(p.endswith('after=4&epoch=raw') for p in paths[1:])

def selection_bridge(monkeypatch,reply):
    """Bridge in id mode with a fresh source; the detector answers with `reply`."""
    import io,json,urllib.error
    b=Bridge('http://camera:8098','',{},'id');b.at=time.monotonic()
    b.sim={'armed':False,'landed':True,'telemetry_fresh':True}
    b.source={'age_ms':1,'stats':{'instance':'proc'},'frame':{'epoch':'oak-rgb:abc:0'},'lock':{'id':None},
              'tracks':[{'id':8,'label':'suitcase','conf':.7,'coasting':False,'x':150,'y':300,'w':60,'h':80}]}
    calls=[]
    def fake(req,timeout=None):
        calls.append(req.full_url)
        if isinstance(reply,int):raise urllib.error.HTTPError(req.full_url,reply,'x',{},io.BytesIO(json.dumps({'ok':False,'reason':'track_coasting'}).encode()))
        b.source['lock']={'id':reply.get('id')}
        return io.BytesIO(json.dumps(reply).encode())
    monkeypatch.setattr('fixed_camera_bridge.urlopen',fake);return b,calls

def test_id_selection_sends_identity_and_never_the_click_route(monkeypatch):
    b,calls=selection_bridge(monkeypatch,{'ok':True,'id':8,'epoch':'oak-rgb:abc:0','instance':'proc'})
    b.choose(8,'oak-rgb:abc:0')
    assert calls==['http://camera:8098/api/select_track?id=8&epoch=oak-rgb%3Aabc%3A0&instance=proc']

@pytest.mark.parametrize('reply,message',[(409,'track_coasting'),(404,'--selection click'),
    ({'ok':True,'id':7,'epoch':'oak-rgb:abc:0','instance':'proc'},'different selection'),
    ({'ok':True,'id':8,'epoch':'oak-rgb:abc:0','instance':'restarted'},'different selection')])
def test_id_selection_failures_are_reported_not_worked_around(monkeypatch,reply,message):
    b,calls=selection_bridge(monkeypatch,reply)
    with pytest.raises(ValueError,match=message):b.choose(8,'oak-rgb:abc:0')
    assert len(calls)==1 and 'mono_click' not in calls[0]

def test_selection_mode_is_explicit():
    with pytest.raises(ValueError):Bridge('','',{},'auto')
    assert Bridge('','',{}).selection=='click' and Bridge('','',{},'id').snapshot()['selection_mode']=='id'
