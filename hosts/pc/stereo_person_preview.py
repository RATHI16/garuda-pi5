"""Unvalidated torso pixel statistics, never a control measurement."""
import math
import numpy as np

def torso_stat(depth, box):
    if len(box)!=4 or not all(math.isfinite(v) for v in box):return {'reason':'Invalid detection box'}
    H,W=depth.shape;region=torso_region(box,W,H)
    if region is None:return {'reason':'Torso region outside image'}
    x0,y0,x1,y1=region
    return region_stat(depth[y0:y1,x0:x1],region)

def torso_region(box,W,H):
    """Central torso part of a detection box as integer (x0,y0,x1,y1), or None."""
    if len(box)!=4 or not all(math.isfinite(v) for v in box):return None
    x,y,w,h=box
    x0,x1=max(0,round(x+.3*w)),min(W,round(x+.7*w))
    y0,y1=max(0,round(y+.2*h)),min(H,round(y+.55*h))
    return None if x1<=x0 or y1<=y0 else (x0,y0,x1,y1)

def region_stat(roi,region):
    """Statistic over the pixels of one region; `roi` holds exactly that region."""
    x0,y0,x1,y1=region
    if roi.shape!=(y1-y0,x1-x0):return {'reason':'Region size mismatch'}
    valid=roi[roi>0]
    if valid.size<30:return {'reason':'Too few stereo pixels in torso','pixels':int(valid.size)}
    p10,median,p90=np.percentile(valid,[10,50,90])/1000
    return {'z_m':float(median),'p10_m':float(p10),'p90_m':float(p90),
            'pixels':int(valid.size),'coverage':float(valid.size/roi.size),'roi':[x0,y0,x1,y1],
            'reason':'Median valid torso-region stereo pixels; unvalidated',
            'measurement_qualified':False}
