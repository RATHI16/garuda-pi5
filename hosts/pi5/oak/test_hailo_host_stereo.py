import numpy as np
import pytest
pytest.importorskip('cv2')
from hailo_host_stereo import prepare,restore

def test_crop_and_pixel_disparity_scaling():
    image=np.zeros((400,640),np.uint8)
    converted,crop=prepare(image)
    assert converted.shape==(368,1232,3) and converted.dtype==np.uint8
    assert crop==(104,191,1.925)
    restored=restore(np.full((368,1232),19.25,np.float32),image.shape,crop)
    assert np.allclose(restored[104:295],10)
    assert not restored[:104].any() and not restored[295:].any()

def test_invalid_prediction_and_shape():
    _,crop=prepare(np.zeros((400,640),np.uint8))
    assert not restore(np.full((368,1232),np.nan), (400,640),crop).any()
    with pytest.raises(ValueError):restore(np.zeros((20,20)),(400,640),crop)
    with pytest.raises(ValueError):prepare(np.zeros((400,640,3),np.uint8))

def test_model_input_order_float_output_and_release(monkeypatch,tmp_path):
 import sys,types
 from hailo_host_stereo import StereoNetModel
 events=[];buffers={}
 class Port:
  def __init__(self,name):self.name=name;self.shape=(368,1232,1 if name.endswith('conv53') else 3)
  def set_format_type(self,v):events.append(('format',self.name,v))
  def set_buffer(self,v):buffers[self.name]=v
 class Binding:
  def input(self,n):return Port(n)
  def output(self,n):return Port(n)
 class Config:
  def create_bindings(self):return Binding()
  def run(self,b,t):buffers['stereonet/conv53'].fill(12)
  def shutdown(self):events.append('shutdown')
 class Model:
  input_names=['stereonet/input_layer1','stereonet/input_layer2'];output_names=['stereonet/conv53']
  def set_batch_size(self,n):pass
  def input(self,n):return Port(n)
  def output(self,n):return Port(n)
  def configure(self):return Config()
 class VD:
  @staticmethod
  def create_params():return None
  def __init__(self,p):pass
  def create_infer_model(self,p):return Model()
  def release(self):events.append('release')
 monkeypatch.setitem(sys.modules,'hailo_platform',types.SimpleNamespace(VDevice=VD,FormatType=types.SimpleNamespace(FLOAT32='float32')))
 path=tmp_path/'model.hef';path.write_bytes(b'test')
 model=StereoNetModel(path);l=np.zeros((368,1232,3),np.uint8);r=np.ones_like(l)
 assert (model.infer(l,r)==12).all()
 assert buffers['stereonet/input_layer1'] is l and buffers['stereonet/input_layer2'] is r
 assert ('format','stereonet/conv53','float32') in events
 model.close();assert events[-2:]==['shutdown','release']
 model.close();assert events.count('release')==1
