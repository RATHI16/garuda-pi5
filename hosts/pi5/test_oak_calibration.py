import io
import json
import zipfile
import numpy as np
import pytest
cv2 = pytest.importorskip("cv2")
from oak_calibration import Workspace, setup, object_points, detect, atomic_json

CONFIG=dict(goal='practice',name='Synthetic test',operator='test',camera='synthetic',board_id='board',
            tape_reference='synthetic camera origin to board',pattern='checkerboard',columns=9,rows=6,
            pitch_mm=25,board_condition='rigid_flat')


def board():
    image=np.full((720,1280,3),220,np.uint8)
    for y in range(7):
        for x in range(10):
            image[150+y*50:150+(y+1)*50,300+x*50:300+(x+1)*50]=0 if (x+y)%2==0 else 255
    return image


class Camera:
    def __init__(self):self.seq=0;self.epoch='test';self.image=board()
    def __call__(self):
        self.seq+=1;image=self.image.copy();image[0,0]=self.seq
        return image,np.full((720,1280),2000,np.uint16),dict(mode='raw_rgb',epoch=self.epoch,sequence=self.seq,
                                  calibration_id='synthetic',geometry=None)


def test_pattern_detection_and_export_preserve_images(tmp_path):
    w=Workspace(tmp_path,Camera());s=w.create(CONFIG)
    s=w.capture(s['id'],dict(split='tune',distance_m=2.0,note='<script>test</script>'))
    c=s['captures'][0]
    assert c['pattern_found'] and len(c['points_px'])==54
    assert c['board_depth']['median_m']==2 and c['board_depth']['valid_fraction']==1
    with zipfile.ZipFile(io.BytesIO(w.export(s['id']))) as z:
        assert c['image_file'] in z.namelist() and c['depth_file'] in z.namelist()
        d=cv2.imdecode(np.frombuffer(z.read(c['depth_file']),np.uint8),cv2.IMREAD_UNCHANGED)
        assert d.dtype==np.uint16 and np.all(d==2000)
        assert json.loads(z.read('session.json'))['applied'] is False


def test_duplicate_and_epoch_change_rejected(tmp_path):
    camera=Camera();w=Workspace(tmp_path,camera);s=w.create(CONFIG);w.capture(s['id'],dict(split='tune'))
    camera.seq=0
    with pytest.raises(ValueError,match='duplicate'):w.capture(s['id'],dict(split='validate'))
    camera.epoch='restarted'
    with pytest.raises(ValueError,match='changed'):w.capture(s['id'],dict(split='tune'))
    assert len(w.load(s['id'])['captures'])==1


def test_bent_board_can_record_but_not_solve(tmp_path):
    w=Workspace(tmp_path,Camera());s=w.create(dict(CONFIG,board_condition='bent'))
    s=w.capture(s['id'],dict(split='tune'))
    assert not s['captures'][0]['geometry_eligible']
    with pytest.raises(ValueError,match='rigid'):w.solve(s['id'])


@pytest.mark.parametrize('field,value',[('columns',2.5),('rows',True),('pitch_mm',float('nan')),
                                       ('pattern','unknown'),('board_condition','yes')])
def test_setup_rejects_bad_metadata(field,value):
    with pytest.raises(ValueError):setup(dict(CONFIG,**{field:value}))


def test_session_path_cannot_escape(tmp_path):
    w=Workspace(tmp_path)
    with pytest.raises(ValueError):w.load('../secret')


def test_distance_rejects_nonfinite(tmp_path):
    w=Workspace(tmp_path,Camera());s=w.create(CONFIG)
    with pytest.raises(ValueError):w.capture(s['id'],dict(split='tune',distance_m=float('inf')))
    assert not w.load(s['id'])['captures']


def synthetic_session(w):
    s=w.create(CONFIG);s['binding']=dict(mode='raw_rgb');obj=object_points(CONFIG)
    k=np.array([[950.,0,640],[0,960,360],[0,0,1]])
    rng=np.random.default_rng(21)
    for i in range(16):
        r=rng.uniform(-.4,.4,3);t=np.array([rng.uniform(-.22,.1),rng.uniform(-.15,.08),rng.uniform(.65,1.4)])
        points,_=cv2.projectPoints(obj,r,t,k,np.zeros(5))
        s['captures'].append(dict(id=str(i),split='tune' if i<12 else 'validate',pattern_found=True,
                                 points_px=points.reshape(-1,2).tolist(),image_size=[1280,720]))
    atomic_json(w.directory(s['id'])/'session.json',s)
    return s,k


def test_candidate_from_synthetic_views_uses_held_out_points(tmp_path):
    w=Workspace(tmp_path);s,k=synthetic_session(w);r=w.solve(s['id'])
    assert np.allclose(r['camera_matrix'],k,atol=.2)
    assert len(r['validation'])==4 and max(x['rms_px'] for x in r['validation'])<.01
    assert not r['applied'] and not r['geometry_verified']
    assert set(r['tune_ids']).isdisjoint(r['validation_ids'])


def test_solver_rejects_pose_leakage(tmp_path):
    w=Workspace(tmp_path);s,_=synthetic_session(w)
    s['captures'][-1]['points_px']=s['captures'][0]['points_px']
    atomic_json(w.directory(s['id'])/'session.json',s)
    with pytest.raises(ValueError,match='duplicates'):w.solve(s['id'])


def test_solver_requires_enough_views(tmp_path):
    w=Workspace(tmp_path);s=w.create(CONFIG)
    with pytest.raises(ValueError,match='12'):w.solve(s['id'])


def test_asymmetric_pattern_definition():
    p=object_points(dict(CONFIG,columns=3,rows=2,pattern='asymmetric_circles',pitch_mm=1000))
    assert p[:,:2].tolist()==[[0,0],[2,0],[4,0],[1,1],[3,1],[5,1]]


def test_http_origin_and_path_boundaries(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from oak_calibration import handler
    workspace=Workspace(tmp_path,Camera())
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(workspace))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        data=json.dumps(CONFIG).encode()
        with pytest.raises(HTTPError) as e:urlopen(Request(base+'/api/sessions',data,headers={'Content-Type':'application/json'}))
        assert e.value.code==403
        with urlopen(Request(base+'/api/sessions',data,headers={'Content-Type':'application/json','Origin':base})) as r:s=json.load(r)
        with urlopen(base+'/api/sessions') as r:assert len(json.load(r))==1
        capture_data=json.dumps(dict(split='tune')).encode()
        with urlopen(Request(base+'/api/session/'+s['id']+'/capture',capture_data,headers={'Content-Type':'application/json','Origin':base})) as r:record=json.load(r)['captures'][0]
        (tmp_path/s['id']/(record['id']+'-depth-preview.jpg')).unlink()
        # Saved immutable thumbnails must work even while a live/capture operation holds the lock.
        workspace.lock.acquire()
        try:
            for suffix in ('-preview.jpg','-depth-preview.jpg'):
                with urlopen(base+'/api/session/'+s['id']+'/'+record['id']+suffix) as r:assert r.read().startswith(b'\xff\xd8')
        finally:workspace.lock.release()
        with pytest.raises(HTTPError):urlopen(base+'/api/session/'+s['id']+'/../session.json')
        with urlopen(base+'/api/session/'+s['id']+'/export') as r:assert zipfile.is_zipfile(io.BytesIO(r.read()))
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('pattern',['circles','asymmetric_circles'])
def test_circle_grid_detection(pattern):
    config=dict(CONFIG,pattern=pattern,columns=4,rows=7,pitch_mm=1000)
    image=np.full((720,1280,3),255,np.uint8)
    for x,y,_ in object_points(config):cv2.circle(image,(int(300+x*40),int(150+y*40)),10,(0,0,0),-1)
    result=detect(image,config)
    assert result is not None and result.shape==(28,2)


def test_camera_restart_between_health_and_frame_is_rejected(monkeypatch):
    import time
    import oak_calibration as module
    class Response(io.BytesIO):pass
    health=Response(json.dumps(dict(epoch='old',stereo_enabled=False)).encode())
    frame=Response(bytes(1280*720*3));frame.headers={'X-Oak-Width':'1280','X-Oak-Height':'720',
        'X-Oak-Format':'BGR888','Content-Length':str(1280*720*3),'X-Oak-Capture-Ns':str(time.monotonic_ns()),
        'X-Oak-Epoch':'new','X-Oak-Sequence':'1'}
    responses=iter([health,frame]);monkeypatch.setattr(module,'urlopen',lambda *a,**k:next(responses))
    with pytest.raises(ValueError,match='restarted'):module.read_camera()


def test_new_depth_session_requires_tape_and_matching_depth(tmp_path):
    camera=Camera();w=Workspace(tmp_path,camera);s=w.create(dict(CONFIG,goal='depth'))
    with pytest.raises(ValueError,match='tape distance'):w.capture(s['id'],dict(split='tune'))
    assert camera.seq==0
    def rgb_only():
        i,d,m=camera();return i,None,m
    w.camera=rgb_only
    with pytest.raises(ValueError,match='matching depth'):w.capture(s['id'],dict(split='tune',distance_m=2))
    assert not w.load(s['id'])['captures']


def test_live_preview_pair_cache_and_depth_colour(tmp_path):
    import time
    from oak_calibration import depth_preview
    camera=Camera()
    def feed():
        i,d,m=camera();m['capture_host_ns']=time.monotonic_ns();return i,d,m
    w=Workspace(tmp_path,feed);a=w.live();b=w.live()
    assert a==b and camera.seq==1 and a['depth_present']
    assert a['rgb'].startswith('data:image/jpeg;base64,') and a['depth'].startswith('data:image/jpeg;base64,')
    assert a['valid_fraction']==1
    assert not depth_preview(np.zeros((2,2),np.uint16)).any()
