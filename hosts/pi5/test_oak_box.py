"""Pi-side consumer of the owner's /box-depth route: answer reader and per-frame measurement."""
import io,json,struct
from urllib.error import HTTPError
import numpy as np
import pytest
from oak_box import BoxStereo,read_box_depth,refusal_reason
from oak_distance import torso_roi,Params

BOX=(500,100,240,560);KEY=('E',41);CAPTURE=123456789
GEOMETRY=dict(schema_version=1,epoch='E',calibration_id='cal',width=1280,height=720,fx=790.,fy=790.,cx=640.,cy=360.,distortion='none',source='host-opencv-factory-calibration',reference_frame='unverified',verified=False)

def region():
    roi,_=torso_roi(BOX,None,(720,1280),Params());return tuple(int(v) for v in roi)

def answer(reg,value=1500,**change):
    x0,y0,x1,y1=reg;w,h=x1-x0,y1-y0
    meta=dict(schema='garuda.oak-box-depth.1',epoch='E',roi=list(reg),width=w,height=h,format='Z16LE',length=w*h*2,depth_backend='pi-sgbm-box',
        preview_only=True,measurement_qualified=False,left_right_skew_ns=13_000,rgb_span_ns=100_000,rgb=dict(seq=41,device_ns=1,host_ns=CAPTURE),
        calibration_id='cal',geometry=GEOMETRY,depth_reference_frame='unverified')
    meta.update(change);blob=json.dumps(meta).encode()
    return struct.pack('>I',len(blob))+blob+np.full((h,w),value,'<u2').tobytes()

class Response(io.BytesIO):
    def __init__(self,body):super().__init__(body);self.headers={'Content-Length':str(len(body))}
    def __enter__(self):return self
    def __exit__(self,*a):return False

def stereo(body=None,error=None):
    urls=[]
    def opener(url,timeout):
        urls.append(url)
        if error:raise error
        return Response(body)
    return BoxStereo(opener=opener),urls

def test_measures_the_estimators_own_region_of_this_frame():
    reg=region();b,urls=stereo(answer(reg))
    block=b.measure(KEY,CAPTURE,BOX)
    x0,y0,x1,y1=reg
    assert urls==[f'http://127.0.0.1:8096/box-depth?epoch=E&rgb_host_ns={CAPTURE}&x0={x0}&y0={y0}&x1={x1}&y1={y1}']
    assert block['status']=='ok' and block['z_m']==1.5 and block['pair']=={'epoch':'E','rgb_seq':41,'pair_id':41}
    assert block['measurement_qualified'] is False and block['preview_only'] is True and block['source']=='host-box-stereo'
    assert block['geometry']==GEOMETRY and block['calibration_id']=='cal' and tuple(block['roi'])==reg
    assert b.counters['readings']==1 and b.counters['requests']==1

def test_nothing_from_an_earlier_frame_survives():
    reg=region();b,_=stereo(answer(reg));b.measure(KEY,CAPTURE,BOX)
    b.opener=lambda url,timeout:(_ for _ in ()).throw(OSError('down'))
    block=b.measure(('E',42),CAPTURE+1,BOX)
    assert block['status']=='unavailable' and block['reason']=='stereo_unavailable' and block['z_m'] is None

def test_no_request_without_a_visible_person_target():
    b,urls=stereo(answer(region()))
    assert b.measure(None,CAPTURE,BOX)['reason']=='not_an_oak_pair_frame'
    assert b.measure(KEY,None,BOX)['reason']=='not_an_oak_pair_frame'
    assert b.measure(KEY,CAPTURE,None)['reason']=='no_target'
    assert b.measure(KEY,CAPTURE,BOX,coasting=True)['reason']=='target_coasting'
    assert b.measure(KEY,CAPTURE,BOX,target_class='chair')['reason']=='unsupported_target_class'
    assert b.measure(KEY,CAPTURE,(0,0,float('nan'),5))['status']=='invalid'
    assert urls==[]

def test_refusal_names_the_owners_reason():
    blob=json.dumps({'reason':'frame_not_in_history'}).encode()
    b,_=stereo(error=HTTPError('u',409,'Conflict',{},io.BytesIO(struct.pack('>I',len(blob))+blob)))
    block=b.measure(KEY,CAPTURE,BOX)
    assert block['status']=='unavailable' and block['reason']=='frame_not_in_history' and b.counters['refused']==1
    assert refusal_reason(HTTPError('u',500,'x',{},io.BytesIO(b'')))=='http_500'

@pytest.mark.parametrize('change',[dict(epoch='F'),dict(rgb=dict(seq=40)),dict(preview_only=False),dict(left_right_skew_ns=5_000_001),dict(rgb_span_ns=33_333_334)])
def test_answers_for_another_frame_or_outside_limits_are_not_used(change):
    b,_=stereo(answer(region(),**change));block=b.measure(KEY,CAPTURE,BOX)
    assert block['status']=='unavailable' and block['z_m'] is None

def test_empty_patch_is_reported_by_the_estimator():
    b,_=stereo(answer(region(),value=0));block=b.measure(KEY,CAPTURE,BOX)
    assert block['status']=='invalid' and block['reason']=='no_valid_depth' and block['z_m'] is None

def test_reader_is_shared_with_the_pc_side():
    reg=(100,50,140,110);body=answer(reg)
    meta,patch=read_box_depth(io.BytesIO(body),len(body),reg);assert patch.shape==(60,40)

def sparse_answer(reg,value=1500):
    """One valid pixel in every 2x2 block: what a half-resolution projection looks like."""
    x0,y0,x1,y1=reg;w,h=x1-x0,y1-y0;d=np.zeros((h,w),'<u2');d[::2,::2]=value
    body=answer(reg);(n,)=struct.unpack('>I',body[:4]);return body[:4+n]+d.tobytes()

def test_sparse_projection_is_judged_on_the_stereo_grid():
    from oak_distance import reduce_nearest,estimate_region_z
    reg=region();b,_=stereo(sparse_answer(reg));block=b.measure(KEY,CAPTURE,BOX)
    # A quarter of colour pixels are filled, every stereo-grid cell is: full coverage, a reading.
    assert block['status']=='ok' and block['z_m']==1.5 and block['coverage']==1.0 and block['coverage_grid']==2 and tuple(block['roi'])==reg

def test_reduce_nearest_takes_the_nearest_valid_and_keeps_empty_blocks_empty():
    from oak_distance import reduce_nearest
    d=np.array([[0,900,0,0,7],[800,0,0,0,0],[0,0,5,0,0]],np.uint16)
    assert reduce_nearest(d,2).tolist()==[[800,0,7],[0,5,0]]

def test_an_owner_without_host_stereo_reads_as_stereo_not_running():
    b,_=stereo(error=HTTPError('u',404,'Not Found',{},io.BytesIO(b'')))
    block=b.measure(KEY,CAPTURE,BOX)
    assert block['status']=='unavailable' and block['reason']=='stereo_unavailable' and b.counters['refused']==1
