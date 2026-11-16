#!/usr/bin/env python3
"""Compare the owner's /box-depth answer with its full depth map for the same colour
frame and region. Agreement between two unvalidated previews; not an accuracy test.
Runs on the Pi against the loopback owner during a host-stereo session."""
import argparse,json,struct,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import numpy as np

def envelope(body):
    (n,)=struct.unpack('>I',body[:4]);return json.loads(body[4:4+n]),body[4+n:]

def compare(full,patch,region):
    """Pure; tested offline. Both arrays are uint16 millimetres, 0 = no value."""
    x0,y0,x1,y1=region;crop=full[y0:y1,x0:x1];both=(crop>0)&(patch>0)
    diff=patch[both].astype(np.int32)-crop[both].astype(np.int32)
    med=lambda a:float(np.median(a)) if a.size else None
    return dict(map_valid=int(np.count_nonzero(crop)),box_valid=int(np.count_nonzero(patch)),both_valid=int(both.sum()),pixels=int(crop.size),
        map_median_mm=med(crop[crop>0]),box_median_mm=med(patch[patch>0]),
        diff_median_mm=med(diff),diff_abs_p90_mm=float(np.percentile(np.abs(diff),90)) if diff.size else None,
        within_20mm=float(np.mean(np.abs(diff)<=20)) if diff.size else None)

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--base',default='http://127.0.0.1:8096')
    p.add_argument('--samples',type=int,default=60);p.add_argument('--regions',default='560,200,720,420;300,150,420,330;820,260,1000,560')
    a=p.parse_args();regions=[tuple(int(v) for v in r.split(',')) for r in a.regions.split(';')];rows=[];cursor='';refused={}
    deadline=time.monotonic()+a.samples*1.5+30
    while len(rows)<a.samples and time.monotonic()<deadline:
        try:
            with urlopen(a.base+'/depth-preview?enc=raw'+cursor,timeout=4) as r:
                if r.status==204:continue
                meta,raw=envelope(r.read())
        except HTTPError as e:
            refused['map HTTP '+str(e.code)]=refused.get('map HTTP '+str(e.code),0)+1;time.sleep(.5);continue
        cursor=f"&after={meta['pair_id']}&epoch={meta['epoch']}"
        full=np.frombuffer(raw,dtype='<u2').reshape(720,1280);region=regions[len(rows)%len(regions)]
        x0,y0,x1,y1=region;started=time.monotonic()
        try:
            with urlopen(a.base+f"/box-depth?epoch={meta['epoch']}&rgb_host_ns={meta['rgb']['capture_host_ns']}&x0={x0}&y0={y0}&x1={x1}&y1={y1}",timeout=2) as r:
                info,payload=envelope(r.read())
        except HTTPError as e:
            reason=envelope(e.read())[0].get('reason');refused[reason]=refused.get(reason,0)+1;continue
        patch=np.frombuffer(payload,dtype='<u2').reshape(y1-y0,x1-x0)
        rows.append(dict(region=list(region),request_ms=(time.monotonic()-started)*1000,compute_ms=info['compute_ns']/1e6,
            stages_ms={k:info[k]/1e6 for k in ('rectify_ns','match_ns','project_ns')},strip=info['strip'],
            same_mono=info['left']['seq']==meta['depth']['seq'],rgb_seq=info['rgb']['seq'],**compare(full,patch,region)))
    a.out.write_text(json.dumps(dict(rows=rows,refused=refused),indent=1))
    med=lambda k:float(np.median([r[k] for r in rows if r[k] is not None])) if any(r[k] is not None for r in rows) else None
    print(json.dumps(dict(samples=len(rows),refused=refused,compute_ms=med('compute_ms'),request_ms=med('request_ms'),diff_median_mm=med('diff_median_mm'),
        diff_abs_p90_mm=med('diff_abs_p90_mm'),within_20mm=med('within_20mm'),same_mono=sum(r['same_mono'] for r in rows))))
if __name__=='__main__':main()
