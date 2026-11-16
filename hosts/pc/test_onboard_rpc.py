"""Real loopback requests through production handlers, with the NPU stubbed."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi5'))
import threading
from types import SimpleNamespace
import numpy as np
import pytest
from http.server import HTTPServer
from sim_detector_server import make_handler
from sim_onboard import SimOnboard
from remote_hailo import RemoteHailo


@pytest.fixture
def rpc():
    identity=dict(instance='pi',engine='hailo10h-rpc',sha256='model',name='Stub Hailo',onboard='box-height-shadow-v1')
    detector=SimpleNamespace(infer=lambda image:[SimpleNamespace(x=600.,y=200.,w=150.,h=200.,conf=.9,class_id=0)])
    cache=SimpleNamespace(get=lambda:(40,0),snapshot=lambda:{})
    h=make_handler(detector,identity,cache,lambda:None,SimOnboard('pi'),lambda *args:None)
    server=HTTPServer(('127.0.0.1',0),h)
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    yield RemoteHailo('http://127.0.0.1:'+str(server.server_port))
    server.shutdown();server.server_close();t.join()


def test_real_infer_and_shadow_routes_share_frame_identity(rpc):
    rows=rpc.infer(np.zeros((720,1280,3),np.uint8))
    row=rows[0]
    obs=dict(frame_w=1280,frame_h=720,age_ms=30,range_source='box_height',stereo=None,identity=None,
             target=dict(id=7,x=row['x'],y=row['y'],w=row['w'],h=row['h'],conf=.9,coasting=False))
    out=rpc.onboard(obs,dict(seq=1,epoch='gazebo',selection_gen=1))
    assert out['valid'] and out['instance']=='pi' and out['frame_seq']==1 and out['target_id']==7
    assert out['transmit'] is False and out['range_m'] is None
    with pytest.raises(Exception):rpc.onboard(obs,dict(seq=1,epoch='gazebo',selection_gen=1))  # consumed inference cannot replay
