#!/usr/bin/env python3
"""Bench-only StereoNet comparison on saved host-stereo pairs; no flight output.
Uses official HEF under existing models/stereonet_h10.md evaluation restriction.
FLOAT32 output requests HailoRT dequantization; raw UINT8 codes are not disparity.
"""
import argparse,json,time
from pathlib import Path
import cv2,numpy as np
from hailo_platform import VDevice,FormatType


from hailo_host_stereo import prepare,restore

def main():
 p=argparse.ArgumentParser();p.add_argument('--hef',required=True);p.add_argument('--pairs',required=True);p.add_argument('--out',required=True);p.add_argument('--yolo-hef');a=p.parse_args()
 for path in [a.hef]+([a.yolo_hef] if a.yolo_hef else []):
  if not Path(path).is_file():raise FileNotFoundError(path)
 cv2.setNumThreads(2);result={'bench_only':True,'accuracy_validated':False,'records':[]}
 with VDevice(VDevice.create_params()) as vd:
  im=vd.create_infer_model(a.hef);im.set_batch_size(1)
  names=list(im.input_names);on=list(im.output_names)[0]
  im.output(on).set_format_type(FormatType.FLOAT32)
  result['inputs']=names;result['output']=on;result['output_type']='FLOAT32-dequantized'
  cm=im.configure()
  yolo=None
  if a.yolo_hef:
   from hailo_yolo26 import HailoYolo26
   yolo=HailoYolo26(a.yolo_hef,vdevice=vd)
  for file in sorted(Path(a.pairs).glob('pair-*.npz')):
   data=np.load(file);l,crop=prepare(data['rectified_left']);r,_=prepare(data['rectified_right'])
   for swap in (False,True):
    times=[]
    for i in range(4):
     b=cm.create_bindings();left='stereonet/input_layer1';right='stereonet/input_layer2'
     b.input(left).set_buffer(r if swap else l);b.input(right).set_buffer(l if swap else r)
     output=np.empty((368,1232,1),np.float32);b.output(on).set_buffer(output)
     start=time.perf_counter();cm.run([b],5000);stereo_ms=(time.perf_counter()-start)*1000
     yolo_ms=None
     if yolo:
      ys=time.perf_counter();detections=yolo.infer(data['rgb']);yolo_ms=(time.perf_counter()-ys)*1000
     if i:times.append(dict(stereo_ms=stereo_ms,yolo_ms=yolo_ms,total_ms=(time.perf_counter()-start)*1000))
    disp=restore(output[:,:,0],data['disparity'].shape,crop)
    valid=np.isfinite(disp)&(disp>0);ref=data['disparity'];overlap=valid&(ref>0)
    # Agreement is not accuracy: CPU output is not ground truth.
    row=dict(pair=file.name,swapped=swap,timing=times,positive_fraction=float(valid.mean()),median_disparity_px=float(np.median(disp[valid])) if valid.any() else None,
       sgbm_overlap_pixels=int(overlap.sum()),median_abs_difference_from_sgbm_px=float(np.median(np.abs(disp[overlap]-ref[overlap]))) if overlap.any() else None)
    result['records'].append(row)
    np.save(Path(a.out).with_name(file.stem+('-swap' if swap else '')+'-disparity.npy'),disp)
  cm.shutdown()
  if yolo:yolo.cm.shutdown()
 Path(a.out).write_text(json.dumps(result,indent=2));print(json.dumps(result))
if __name__=='__main__':main()
