import numpy as np
from hailo_osnet import crop_boxes,normalise,MAX_CROPS

def test_crops_are_clipped_limited_and_slivers_are_refused():
    shape=(720,1280,3)
    assert crop_boxes(shape,[(100,50,200,400)])==[(100,50,300,450)]
    assert crop_boxes(shape,[(-40,-10,200,400)])==[(0,0,160,390)]              # clipped to the image
    assert crop_boxes(shape,[(1270,100,200,400)])==[None]                      # 10 px wide after clipping
    assert crop_boxes(shape,[(100,700,200,400)])==[None]                       # 20 px high after clipping
    assert crop_boxes(shape,[(0,0,float('nan'),10)])==[None] and crop_boxes(shape,[None,'x',(1,2,3)])==[None,None,None]
    many=[(10*i,0,100,300) for i in range(7)]
    assert len(crop_boxes(shape,many))==MAX_CROPS==4

def test_normalise():
    v=normalise([3.,4.]);assert np.allclose(v,[.6,.8]) and v.dtype==np.float32
    assert normalise(np.zeros(512)) is None and normalise([float('nan'),1.]) is None and normalise([float('inf'),1.]) is None
