"""Exercise the real vision loop and consumer with deterministic pixels/detections.

The Pi command implementation runs locally in this test; no hardware result is claimed.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"pi5"))
import threading
import time
from types import SimpleNamespace
import numpy as np
import pytest
import vision
from sim_onboard import SimOnboard
from sitl_demo_core import DemoControl


def test_vision_selection_frame_binding_and_command_path(monkeypatch):
    image=np.zeros((720,1280,3),np.uint8)
    current=[None]
    pixels=SimpleNamespace(lock=threading.RLock(),depths={},overview=None,
        snapshot=lambda:(current[0],np.ones((720,1280),np.float32)*20,(0,time.monotonic_ns()),1.))
    monkeypatch.setattr(vision,'GazeboPixels',lambda:pixels)
    command=SimOnboard('test-pi')
    class Detector:
        engine='hailo10h-rpc';sha='fake-model';name='Test stub, not hardware'
        def infer(self,rgb):
            command.remember(str(current[0][0]),[dict(x=600,y=200,w=150,h=200,confidence=.9,label='person')])
            return [dict(x=600,y=200,w=150,h=200,confidence=.9,label='person',class_id=0)]
        def onboard(self,obs,frame):
            v.running=False
            return command.compute(dict(request_id=str(current[0][0]),observation=obs,frame=frame))
    v=vision.Vision(Detector(),{});v.configure(dict(mode='onboard'))
    def frame(n):
        current[0]=(n,n,time.monotonic_ns(),image);v.running=True;v.run()
        return v.snapshot()
    s=frame(1)
    assert not s['onboard']['valid'] and s['stats']['capabilities']['mount']=='vehicle'
    assert s['tracks'][0]['range_m'] is None  # never present Gazebo RGBD as stereo
    tid=s['tracks'][0]['id']
    v.configure(dict(onboard_select=tid,epoch=s['frame']['epoch'],instance='test-pi',selection_gen=1))
    s=frame(2);assert s['onboard']['valid'] and s['onboard']['target_id']==tid
    c=DemoControl();c.accept_onboard=True;s['kind']='onboard';c.receive(s,time.monotonic());c.select(tid);c.mode='follow'
    time.sleep(.01);s=frame(3);s['kind']='onboard';c.receive(s,time.monotonic())
    assert c.velocity([0,0,-1.5],time.monotonic(),.05,[1,0,0,0])[0]>0
    v.configure(dict(onboard_select=None,epoch=s['frame']['epoch'],instance='test-pi',selection_gen=1))
    assert 'onboard' not in v.snapshot()
    assert not frame(4)['onboard']['valid']
    with pytest.raises(ValueError):v.configure(dict(onboard_select=tid,epoch='old',instance='test-pi',selection_gen=1))


def test_no_local_cpu_fallback_for_shadow(monkeypatch):
    monkeypatch.setattr(vision,'GazeboPixels',lambda:None)
    v=vision.Vision(SimpleNamespace(engine='ort'),{})
    with pytest.raises(ValueError,match='Pi Hailo'):v.configure(dict(mode='onboard'))


def test_command_rpc_does_not_block_release_or_publish_after_it(monkeypatch):
    image=np.zeros((720,1280,3),np.uint8);entered=threading.Event();finish=threading.Event()
    pixels=SimpleNamespace(snapshot=lambda:((2,2,time.monotonic_ns(),image),np.ones((720,1280),np.float32),(0,time.monotonic_ns()),1.))
    monkeypatch.setattr(vision,'GazeboPixels',lambda:pixels)
    class Detector:
        engine='hailo10h-rpc';sha='fake';name='Stub'
        def infer(self,rgb):return [dict(x=600,y=200,w=150,h=200,confidence=.9,label='person',class_id=0)]
        def onboard(self,obs,frame):
            entered.set();finish.wait(3);v.running=False
            return dict(valid=True,forward_vel=.3,instance='pi',selection_gen=frame['selection_gen'])
    v=vision.Vision(Detector(),{});v.configure(dict(mode='onboard'))
    v.onboard_target=1;v.onboard_generation=1
    worker=threading.Thread(target=v.run);worker.start()
    assert entered.wait(1)
    cleared=threading.Event()
    def clear():
        v.configure(dict(onboard_select=None,selection_gen=2));cleared.set()
    caller=threading.Thread(target=clear);caller.start()
    try:
        assert cleared.wait(.3), 'selection was blocked by command RPC'
        assert 'onboard' not in v.snapshot()
    finally:
        finish.set();caller.join(2);worker.join(2)
    assert not worker.is_alive() and v.onboard_target is None and 'onboard' not in v.snapshot()
