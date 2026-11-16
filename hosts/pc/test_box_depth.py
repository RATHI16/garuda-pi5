"""PC side of the box-only stereo preview: answer validation, reader and bridge job."""
import io,json,struct,sys,time
from pathlib import Path
from urllib.error import HTTPError
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stereo_preview import read_box_depth,BoxDepth,MAX_AGE_MS
from stereo_person_preview import torso_stat,torso_region,region_stat

REGION=(100,50,140,110)

def answer(region=REGION,value=1500,**change):
    x0,y0,x1,y1=region;w,h=x1-x0,y1-y0
    meta=dict(schema='garuda.oak-box-depth.1',roi=list(region),width=w,height=h,format='Z16LE',length=w*h*2,
        depth_backend='pi-sgbm-box',preview_only=True,measurement_qualified=False,left_right_skew_ns=13_000,rgb_span_ns=20_000_000,compute_ns=4_000_000,strip=[200,80])
    meta.update(change);blob=json.dumps(meta).encode()
    return struct.pack('>I',len(blob))+blob+np.full((h,w),value,'<u2').tobytes()

class Response(io.BytesIO):
    def __init__(self,body):super().__init__(body);self.headers={'Content-Length':str(len(body))}
    def __enter__(self):return self
    def __exit__(self,*a):return False

def job(age_ms=100,region=REGION,seq=1):
    return dict(key=['i','oak-rgb:E:1',7],frame=('i','oak-rgb:E:1',seq),owner_epoch='E',capture=123456789,region=region,at=time.monotonic(),age_ms=age_ms)

def test_region_split_keeps_the_torso_statistic():
    d=np.zeros((720,1280),np.uint16);d[100:400,300:500]=2000
    box=[250,50,300,500];region=torso_region(box,1280,720);x0,y0,x1,y1=region
    assert torso_stat(d,box)==region_stat(d[y0:y1,x0:x1],region)
    assert torso_region([0,0,float('nan'),5],1280,720) is None and torso_region([2000,0,10,10],1280,720) is None
    assert 'z_m' not in region_stat(np.zeros((3,3),np.uint16),region)

def test_read_box_depth_accepts_a_valid_answer():
    body=answer();meta,patch=read_box_depth(io.BytesIO(body),len(body),REGION)
    assert patch.shape==(60,40) and int(patch[0,0])==1500 and meta['strip']==[200,80]

@pytest.mark.parametrize('change',[dict(schema='x'),dict(preview_only=False),dict(measurement_qualified=True),dict(depth_backend='pi-sgbm'),
    dict(roi=[0,0,40,60]),dict(format='Z16BE'),dict(length=5),dict(left_right_skew_ns=5_000_001),dict(rgb_span_ns=33_333_334),
    dict(left_right_skew_ns=None),dict(rgb_span_ns=1.5)])
def test_read_box_depth_refuses_contract_breaches(change):
    body=answer(**change)
    with pytest.raises(ValueError):read_box_depth(io.BytesIO(body),len(body),REGION)

def test_read_box_depth_refuses_truncated_and_trailing():
    body=answer()
    with pytest.raises(ValueError):read_box_depth(io.BytesIO(body[:-1]),len(body),REGION)
    with pytest.raises(ValueError):read_box_depth(io.BytesIO(body+b'x'),len(body),REGION)
    with pytest.raises(ValueError):read_box_depth(io.BytesIO(body),len(body)+1,REGION)

def test_reader_stores_a_reading_for_the_selected_person_only():
    b=BoxDepth('http://x');urls=[]
    def opener(url,timeout):urls.append(url);return Response(answer())
    j=job();b.step(j,opener)
    assert urls==['http://x/box-depth?epoch=E&rgb_host_ns=123456789&x0=100&y0=50&x1=140&y1=110']
    d=b.snapshot(j['key'])
    assert d['z_m']==1.5 and d['method']=='box' and d['measurement_qualified'] is False and 100<=d['age_ms']<MAX_AGE_MS
    assert 'z_m' not in b.snapshot(['i','oak-rgb:E:1',8]) and 'z_m' not in b.snapshot(None)
    assert b.snapshot(j['key'])['health']['counts']=={'readings':1}

def test_reading_expires_and_stale_jobs_are_not_requested():
    b=BoxDepth('http://x');j=job(age_ms=650);b.step(j,lambda url,timeout:Response(answer()))
    b.reading_at-=.1
    assert 'z_m' not in b.snapshot(j['key'])
    def never(url,timeout):raise AssertionError('must not request')
    b.step(job(age_ms=700),never)
    assert b.snapshot(None)['health']['counts']['stale_before_request']==1

def test_refusal_and_errors_give_no_reading_and_a_named_reason():
    b=BoxDepth('http://x')
    blob=json.dumps({'reason':'frame_not_in_history'}).encode();body=struct.pack('>I',len(blob))+blob
    def refuse(url,timeout):raise HTTPError(url,409,'Conflict',{},io.BytesIO(body))
    j=job();b.step(j,refuse);d=b.snapshot(j['key'])
    assert 'z_m' not in d and 'frame_not_in_history' in d['reason'] and d['health']['counts']=={'refused: frame_not_in_history':1}
    def broken(url,timeout):raise OSError('down')
    b.step(job(),broken);d=b.snapshot(j['key'])
    assert 'z_m' not in d and d['health']['error']=='OSError: down'
    # An answer with too few valid pixels is not a reading.
    b.step(job(),lambda url,timeout:Response(answer(value=0)))
    assert 'z_m' not in b.snapshot(j['key']) and b.snapshot(None)['health']['counts']['answer_without_reading']==1

def test_submit_keeps_only_the_latest_distinct_frame():
    b=BoxDepth('http://x')
    assert b.submit(job(seq=1)) and not b.submit(job(seq=1)) and b.submit(job(seq=2))
    assert b.job['frame'][2]==2 and b.counts['superseded']==1
    assert b.submit(None) is False and b.snapshot(None)['reason']=='Select a visible person'

def test_bridge_builds_a_job_only_for_a_visible_selected_person():
    from fixed_camera_bridge import Bridge
    b=Bridge('http://c','http://s',{})
    s=dict(age_ms=80,lock={'id':7},stats={'instance':'i'},frame=dict(epoch='oak-rgb:E:9',seq=4,capture_host_ns=555,width=1280,height=720),
        tracks=[dict(id=7,label='person',x=400,y=100,w=200,h=500,conf=.9)])
    j=b.box_job(s,1.0)
    assert j['owner_epoch']=='E' and j['capture']==555 and j['region']==torso_region([400,100,200,500],1280,720) and j['key']==['i','oak-rgb:E:9',7]
    import copy
    for change in [lambda s:s['tracks'][0].update(coasting=True),lambda s:s['tracks'][0].update(label='chair'),lambda s:s['lock'].update(id=8),
                   lambda s:s['frame'].update(epoch='cam:1'),lambda s:s['frame'].update(capture_host_ns=5.5),lambda s:s['frame'].update(width=640),
                   lambda s:s['stats'].clear(),lambda s:s['tracks'][0].update(w=float('inf'))]:
        t=copy.deepcopy(s);change(t);assert b.box_job(t,1.0) is None

def test_version_changes_on_every_outcome_and_wakes_waiters():
    import threading
    b=BoxDepth('http://x');v=b.snapshot(None)['version']
    b.step(job(),lambda url,timeout:Response(answer()));assert b.snapshot(None)['version']==v+1
    def broken(url,timeout):raise OSError('down')
    b.step(job(),broken);assert b.snapshot(None)['version']==v+2
    b.submit(None);b.submit(None);assert b.snapshot(None)['version']==v+3
    t0=time.monotonic();b.wait_change(v,5);assert time.monotonic()-t0<.5          # already changed: returns at once
    t0=time.monotonic();b.wait_change(v+3,.05);assert .04<=time.monotonic()-t0<1  # nothing new: returns at the limit
    waiter=threading.Thread(target=b.wait_change,args=(v+3,5));waiter.start()
    b.step(job(),lambda url,timeout:Response(answer()));waiter.join(2);assert not waiter.is_alive()

def test_transit_time_excludes_only_a_plausible_reported_hold():
    from fixed_camera_bridge import Bridge
    poll=Bridge('http://c','http://s',{});assert poll.transit_ms({'waited_ms':40},50)==50
    w=Bridge('http://c','http://s',{},detector_read='wait')
    s={'waited_ms':40.0};assert w.transit_ms(s,50)==10 and 'waited_ms' not in s
    for bad in [{}, {'waited_ms':60}, {'waited_ms':-1}, {'waited_ms':float('nan')}, {'waited_ms':'3'}]:
        assert w.transit_ms(dict(bad),50)==50
    assert w.counts['wait_unreported']==5
    with pytest.raises(ValueError):Bridge('http://c','http://s',{},detector_read='push')
