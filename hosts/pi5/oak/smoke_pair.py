#!/usr/bin/env python3
"""Bounded loopback smoke test using the independently reviewed consumer parser.

No device access: run against a separately started owner. Saves metadata/statistics only.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak_pair import read_pair, PairCursor


def run(base, seconds):
    start = time.monotonic()
    cursor = PairCursor()
    result = dict(pairs=[], errors=[], elapsed_s=None)
    while time.monotonic()-start < seconds:
        try:
            with urlopen(base+cursor.query(), timeout=3) as r:
                if r.status == 204:
                    continue
                pair=read_pair(r,int(r.headers['Content-Length']))
            cursor.accept(pair)
            m=dict(pair.meta)
            m['age_ms_at_receive']=(time.monotonic_ns()-m['rgb']['capture_host_ns'])/1e6
            if pair.depth_mm is not None:
                valid=pair.depth_mm[pair.depth_mm>0]
                m['valid_depth_fraction']=float(valid.size/pair.depth_mm.size)
                m['median_valid_z_mm']=float(np.median(valid)) if valid.size else None
            result['pairs'].append(m)
        except (HTTPError,URLError,OSError) as e:
            result['errors'].append(dict(elapsed_s=time.monotonic()-start,error=str(e)))
            time.sleep(.25)
    result['elapsed_s']=time.monotonic()-start
    with urlopen(base+'/health',timeout=3) as r: result['health']=json.load(r)
    # Check payload omission, bad query, and diagnostics without retaining image data.
    with urlopen(base+'/pair?depth=0',timeout=3) as r:
        p=read_pair(r,int(r.headers['Content-Length']))
        assert p.depth_mm is None and p.meta['depth_reason']=='omitted_by_request'
    try:
        urlopen(base+'/pair?depth=bad',timeout=3)
        raise AssertionError('bad query accepted')
    except HTTPError as e:
        assert e.code==400
    result['http_checks']='depth=0 and invalid query passed'
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--base',default='http://127.0.0.1:8097')
    ap.add_argument('--seconds',type=float,default=30)
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    r=run(args.base,args.seconds)
    Path(args.out).write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(dict(pairs=len(r['pairs']),paired=sum(x['depth_valid'] for x in r['pairs']),
                         elapsed_s=r['elapsed_s'],health=r['health'])))
