#!/usr/bin/env python3
"""Compare projection kernels on saved disparities; not a depth accuracy test."""
import argparse,hashlib,json,platform,time,sys
from pathlib import Path
import numpy as np
import cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'hosts/pi5/oak'))
from host_stereo import HostStereo
def reference_project(b,disparity):
 # Frozen pre-optimization projection, including nearest-Z collisions and sentinel.
 xyz=cv2.reprojectImageTo3D(disparity.astype(np.float32),b.q)
 valid=(disparity>0)&np.isfinite(xyz).all(axis=2)&(xyz[:,:,2]>0)
 points=xyz[valid]@b.r1
 points=points@b.left_to_rgb[:3,:3].T+b.left_to_rgb[:3,3]
 points=points[np.isfinite(points).all(axis=1)&(points[:,2]>.05)&(points[:,2]<65.535)]
 z=points[:,2];pixels=points@b.rgb_k.T
 uv=np.rint(pixels[:,:2]/z[:,None]).astype(np.int32)
 w,h=b.rgb_size;inside=(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h)
 result=np.full(h*w,65535,dtype=np.uint16)
 np.minimum.at(result,uv[inside,1]*w+uv[inside,0],np.rint(z[inside]*1000).astype(np.uint16))
 result[result==65535]=0
 return result.reshape(h,w)



def main():
 p=argparse.ArgumentParser();p.add_argument('recordings',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--translation-scale',type=float,required=True);p.add_argument('--repeats',type=int,default=10);a=p.parse_args()
 cv2.setNumThreads(2)
 raw=(a.recordings/'calibration.json').read_bytes();c=json.loads(raw);cams=dict(c['factory']['cameraData'])
 def ext(socket,target):
  e=cams[socket]['extrinsics'];assert e['toCameraSocket']==target
  t=np.eye(4);t[:3,:3]=e['rotationMatrix'];t[:3,3]=[e['translation'][k]*a.translation_scale for k in ['x','y','z']];return t
 lr=ext(1,2);la=ext(2,0)@lr;k=[np.array(x) for x in c['k']];d=[np.array(x) for x in c['d']]
 b=HostStereo(k[0],d[0],k[1],d[1],lr,la,k[2],d[2]);rows=[]
 for path in sorted(a.recordings.glob('pair-*.npz')):
  with np.load(path) as x:disparity=x['disparity']
  old=reference_project(b,disparity);v2=b.project_v2(disparity);new=b.project(disparity)
  if np.count_nonzero(old!=v2):raise RuntimeError(f'{path.name}: v2 no longer matches the frozen reference')
  # v3 is not required to be bit-identical: every changed pixel is counted and sized.
  changed=old!=new;both=changed&(old>0)&(new>0)
  delta=dict(output_pixels=int(old.size),nonzero_reference=int(np.count_nonzero(old)),nonzero_v3=int(np.count_nonzero(new)),
   changed_pixels=int(np.count_nonzero(changed)),only_in_reference=int(np.count_nonzero(changed&(old>0)&(new==0))),
   only_in_v3=int(np.count_nonzero(changed&(old==0)&(new>0))),
   max_mm_where_both=int(np.abs(old[both].astype(np.int32)-new[both].astype(np.int32)).max()) if both.any() else 0)
  times={'reference':[],'v2':[],'v3':[]}
  kernels=[('reference',reference_project),('v2',lambda b,d:b.project_v2(d)),('v3',lambda b,d:b.project(d))]
  for i in range(a.repeats):
   for name,fn in kernels[i%3:]+kernels[:i%3]:
    start=time.perf_counter_ns();fn(b,disparity);times[name].append((time.perf_counter_ns()-start)/1e6)
  rows.append(dict(file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),v3_against_reference=delta,milliseconds=times))
 a.out.write_text(json.dumps(dict(machine=platform.machine(),opencv=cv2.__version__,numpy=np.__version__,calibration_sha256=hashlib.sha256(raw).hexdigest(),translation_scale=a.translation_scale,rows=rows),indent=2))
 print(json.dumps(dict(median_ms={name:float(np.median([v for row in rows for v in row['milliseconds'][name]])) for name in ['reference','v2','v3']},
  changed_pixels=sum(r['v3_against_reference']['changed_pixels'] for r in rows),nonzero_reference=sum(r['v3_against_reference']['nonzero_reference'] for r in rows),
  max_mm_where_both=max(r['v3_against_reference']['max_mm_where_both'] for r in rows))))
if __name__=='__main__':main()
