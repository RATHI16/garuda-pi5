"""Camera/replay contract tests; no running Gazebo or inference model required."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from types import SimpleNamespace
import numpy as np
from gazebo_pixels import depth_range, rgb_message
from vision import Replay

def test_padded_rgb_rows_and_bad_pixel_format():
    msg=SimpleNamespace(width=2,height=1,step=8,pixel_format_type=3,data=bytes(range(8)))
    assert rgb_message(msg).tolist()==[[[0,1,2],[3,4,5]]]
    msg.pixel_format_type=13
    try:rgb_message(msg)
    except ValueError:pass
    else:raise AssertionError('Depth must not decode as RGB')

def test_depth_rejects_background_mixture_and_missing_depth():
    box=dict(x=100,y=100,w=100,h=200)
    depth=np.full((720,1280),3.,dtype=np.float32)
    assert depth_range(depth,box)==3
    assert depth_range(None,box) is None
    depth[150:185]=20
    assert depth_range(depth,box) is None

def test_replay_end_is_finite_and_does_not_loop(tmp_path):
    import cv2
    cv2.imwrite(str(tmp_path/'000001.jpg'),np.zeros((8,8,3),dtype=np.uint8))
    r=Replay(dict(path=str(tmp_path),kind='images',fps=30))
    assert r.read().shape==(8,8,3)
    assert r.read() is None and r.done

def test_gap_new_person_same_box_requires_reselection(monkeypatch):
 import vision
 from sitl_demo_core import DemoControl
 monkeypatch.setattr(vision,'GazeboPixels',lambda:None)
 v=vision.Vision(None,{})
 det=dict(x=600,y=250,w=80,h=200,confidence=.9,class_id=0,label='person')
 old=v.tracker.update([det])[0];epoch=v.epoch
 c=DemoControl()
 def data(seq,epoch,tid):return dict(age_ms=0,frame=dict(seq=seq,epoch=epoch,width=1280,height=720),tracks=[dict(id=tid,x=600,y=250,w=80,h=200,conf=.9,coasting=False)])
 c.receive(data(1,epoch,old.id),0);c.select(old.id)
 v.continuity_valid=True;v.last_processed_at=0
 v.check_continuity(.51)
 # A different person in the same pixels can get the same integer ID; epoch is key.
 replacement=v.tracker.update([det])[0]
 assert v.epoch!=epoch
 c.receive(data(2,v.epoch,replacement.id),.52)
 assert c.track(.52) is None and c.lock_state=='RESELECT'
 c.select(replacement.id);assert c.track(.52) is not None

def test_error_clears_state_and_changes_epoch(monkeypatch):
 import vision
 monkeypatch.setattr(vision,'GazeboPixels',lambda:None)
 v=vision.Vision(None,{});v.continuity_valid=True;v.state={'tracks':[{'id':1}]};v.last_processed_at=1
 old=v.epoch;version=v.version
 v.invalidate('RPC timeout',force=True)
 assert v.epoch!=old and v.version!=version and not v.state and v.last_processed_at is None

def test_gap_invalidates_inflight_version_only_once(monkeypatch):
 import vision
 monkeypatch.setattr(vision,'GazeboPixels',lambda:None)
 v=vision.Vision(None,{});v.continuity_valid=True;v.last_processed_at=1
 version=v.version;v.check_continuity(1.6)
 assert v.version!=version
 version=v.version;v.check_continuity(2)
 assert v.version==version

def test_slow_inflight_result_cannot_repopulate_invalidated_epoch(monkeypatch):
 import vision
 clock=[1.1]
 image=np.zeros((720,1280,3),np.uint8)
 pixels=SimpleNamespace(snapshot=lambda:((1,1,1_000_000_000,image),None,None,None))
 monkeypatch.setattr(vision,'GazeboPixels',lambda:pixels)
 monkeypatch.setattr(vision.time,'monotonic',lambda:clock[0])
 class Detector:
  def infer(self,rgb):
   clock[0]=1.7;v.running=False;return []
 v=vision.Vision(Detector(),{});v.paused=False;v.continuity_valid=True;v.last_processed_at=1.
 epoch=v.epoch;v.run()
 assert v.epoch!=epoch and not v.state and v.processed==0

def test_run_inference_exception_invalidates_published_identity(monkeypatch):
 import vision
 pixels=SimpleNamespace(snapshot=lambda:((1,1,1_000_000_000,np.zeros((720,1280,3),np.uint8)),None,None,None))
 monkeypatch.setattr(vision,'GazeboPixels',lambda:pixels)
 monkeypatch.setattr(vision.time,'sleep',lambda _:None)
 class Detector:
  def infer(self,rgb):v.running=False;raise OSError('tunnel lost')
 v=vision.Vision(Detector(),{});v.paused=False;v.continuity_valid=True
 v.state={'tracks':[{'id':1}]};epoch=v.epoch;v.run()
 assert v.epoch!=epoch and not v.state and 'tunnel lost' in v.error
