#!/usr/bin/env python3
"""Record the owner's loopback IMU cursor; never opens the device itself."""
import argparse
import json
from pathlib import Path
import time
from urllib.request import urlopen
from urllib.parse import urlencode

def run(base,seconds,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    epoch=None;after=-1;end=time.monotonic()+seconds;errors=[];overruns=0;resets=0;count=0
    with (out/'samples.jsonl').open('w') as f:
        while time.monotonic()<end:
            try:
                query=urlencode(dict(after=after,epoch=epoch or ''))
                with urlopen(base+'/imu?'+query,timeout=2) as r:data=json.load(r)
                if epoch is not None and data['epoch']!=epoch:resets+=1
                epoch=data['epoch'];overruns+=int(data['cursor_overrun'])
                for row in data['samples']:
                    f.write(json.dumps(dict(row,epoch=epoch))+'\n');count+=1
                after=data['next_id']-1
            except Exception as e:errors.append(dict(t=time.monotonic(),error=str(e)))
            time.sleep(.5)
    result=dict(samples=count,cursor_overruns=overruns,epoch_changes=resets,errors=errors,
                requested_seconds=seconds,base=base)
    (out/'transport.json').write_text(json.dumps(result,indent=2));return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8096');p.add_argument('--seconds',type=float,default=120);p.add_argument('--out',required=True)
    a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('seconds must be 1..3600')
    print(json.dumps(run(a.base,a.seconds,a.out)))
