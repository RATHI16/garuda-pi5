#!/usr/bin/env python3
"""Compare Z/radial hypotheses on an explicitly selected rigid-board rectangle.

Input is an exact /pair binary response, no device access. Both hypotheses use
identical pixels, exact delivered K and plane fitting; no pass/accuracy claim.
The rectangle MUST exclude background and board edges. Geometry is not verified
by a low residual alone. Input images are private local evidence, not Git assets.
"""
import argparse
import io
import json
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'hosts/pi5'))
from oak_pair import read_pair

def plane_residual(points):
    # Robust least-median selection then deterministic trimmed refit, same algorithm
    # for both hypotheses. All-pixel residuals remain reported so trimming is visible.
    rng=np.random.default_rng(7);best=None
    for _ in range(100):
        p=points[rng.choice(len(points),3,replace=False)]
        normal=np.cross(p[1]-p[0],p[2]-p[0]);norm=np.linalg.norm(normal)
        if norm<1e-10:continue
        normal/=norm;offset=normal@p[0];r=np.abs(points@normal-offset)
        score=np.median(r)
        if best is None or score<best[0]:best=(score,r)
    if best is None:raise ValueError('degenerate board points')
    keep=best[1]<=np.quantile(best[1],.9);selected=points[keep];centre=selected.mean(axis=0)
    _,_,vh=np.linalg.svd(selected-centre,full_matrices=False);normal=vh[-1]
    residual=points@normal-normal@centre
    return dict(normal=normal.tolist(),offset_m=float(normal@centre),fit_pixels=int(keep.sum()),
                residual_median_abs_m=float(np.median(np.abs(residual))),
                residual_p95_abs_m=float(np.quantile(np.abs(residual),.95)),
                residual_rms_all_m=float(np.sqrt(np.mean(residual**2))))

def analyse(depth,g,roi):
    x0,y0,x1,y1=roi;h,w=depth.shape
    if not 0<=x0<x1<=w or not 0<=y0<y1<=h:raise ValueError('ROI outside image')
    v,u=np.mgrid[y0:y1:4,x0:x1:4];d=depth[y0:y1:4,x0:x1:4]/1000
    mask=(d>0)&np.isfinite(d);z=d[mask]
    if len(z)<100:raise ValueError('fewer than 100 valid board samples')
    rays=np.column_stack(((u[mask]-g['cx'])/g['fx'],(v[mask]-g['cy'])/g['fy'],np.ones(len(z))))
    return dict(roi=roi,valid_pixels=len(z),sampled_pixels=int(d.size),
                z_hypothesis=plane_residual(rays*z[:,None]),
                radial_hypothesis=plane_residual(rays/np.linalg.norm(rays,axis=1)[:,None]*z[:,None]),
                statement='Hypothesis comparison only; not axis/origin or distance-accuracy verification.')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('pair');p.add_argument('--roi',nargs=4,type=int,required=True);p.add_argument('--out',required=True);a=p.parse_args()
    blob=Path(a.pair).read_bytes();pair=read_pair(io.BytesIO(blob),len(blob));g=pair.meta.get('geometry')
    if pair.depth_mm is None or g is None:p.error('requires depth and bound delivered geometry')
    result=analyse(pair.depth_mm,g,a.roi);result.update(epoch=pair.meta['epoch'],pair_id=pair.meta['pair_id'],geometry=g)
    Path(a.out).write_text(json.dumps(result,indent=2))
