import json,struct
import numpy as np
from oak_box_depth_check import compare,envelope

def test_compare_counts_overlap_and_difference():
    full=np.zeros((20,30),np.uint16);full[5:15,10:20]=1000;patch=np.full((10,10),1010,np.uint16);patch[0]=0;full[14,10:20]=0
    c=compare(full,patch,(10,5,20,15))
    assert (c['map_valid'],c['box_valid'],c['both_valid'],c['pixels'])==(90,90,80,100)
    assert c['diff_median_mm']==10 and c['within_20mm']==1.0

def test_compare_handles_no_overlap():
    c=compare(np.zeros((4,4),np.uint16),np.zeros((2,2),np.uint16),(0,0,2,2))
    assert c['both_valid']==0 and c['diff_median_mm'] is None and c['box_median_mm'] is None

def test_envelope():
    blob=json.dumps({'a':1}).encode();assert envelope(struct.pack('>I',len(blob))+blob+b'xy')==({'a':1},b'xy')
