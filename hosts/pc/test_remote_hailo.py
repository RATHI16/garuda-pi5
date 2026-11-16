import copy
import pytest
from remote_hailo import validate_result

def result():return dict(request_id='one',pixels_sha256='pixels',detector={'sha256':'model'},detections=[dict(class_id=0,label='person',x=10.,y=10.,w=20.,h=30.,confidence=.9)])
def test_same_frame():assert len(validate_result(result(),'one','pixels','model'))==1
@pytest.mark.parametrize('field,value',[('request_id','old'),('pixels_sha256','other')])
def test_wrong_frame(field,value):
 r=result();r[field]=value
 with pytest.raises(ValueError):validate_result(r,'one','pixels','model')
@pytest.mark.parametrize('key,value',[('x',float('nan')),('w',-1),('h',900),('confidence',2)])
def test_bad_boxes(key,value):
 r=result();r['detections'][0][key]=value
 with pytest.raises(ValueError):validate_result(r,'one','pixels','model')
def test_model_changed():
 with pytest.raises(ValueError):validate_result(result(),'one','pixels','new')

def test_detector_restart_rejected():
 r=result();r['detector']['instance']='new'
 with pytest.raises(ValueError):validate_result(r,'one','pixels','model','old')
