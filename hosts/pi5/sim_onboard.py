"""Simulation-only Pi command RPC, bound to the immediately preceding inference.

No flight transport. PC owns rehearsal tracking/selection; this Pi runs the same
pure command law as garuda_mono. Only box-height is supported in this rehearsal.
"""
import math
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'services/garuda-follow'))
from onboard_command import OnboardFollower, limits


class SimOnboard:
    def __init__(self, instance, clock=time.monotonic):
        self.instance=instance;self.clock=clock;self.pending=None;self.key=None
        self.follower=OnboardFollower()

    def remember(self, request_id, rows):
        self.pending=(request_id,rows,self.clock())

    def compute(self, data):
        previous=self.pending;self.pending=None  # one command per inferred image, including failures
        try:
            if not isinstance(data,dict) or previous is None or data.get('request_id')!=previous[0]:
                raise ValueError('command must reference latest inference exactly once')
            frame=data.get('frame',{});obs=data.get('observation',{})
            generation=frame.get('selection_gen')
            if type(generation) is not int or generation<0:raise ValueError('Selection generation required')
            if type(frame.get('seq')) is not int or frame['seq']<0 or not isinstance(frame.get('epoch'),str) or not 1<=len(frame['epoch'])<=128:
                raise ValueError('frame identity missing')
            if obs.get('range_source')!='box_height' or obs.get('stereo') is not None or obs.get('identity') is not None:
                raise ValueError('box-height rehearsal only; no stereo or re-ID claim')
            if (obs.get('frame_w'),obs.get('frame_h'))!=(1280,720):raise ValueError('wrong frame size')
            age=obs.get('age_ms');elapsed=self.clock()-previous[2]
            if type(age) not in (int,float) or not math.isfinite(age) or age<0 or elapsed<0 or elapsed>.4:
                raise ValueError('stale or invalid command request')
            t=obs.get('target')
            if t is not None:
                if not isinstance(t,dict) or type(t.get('id')) is not int or t['id']<0 or t.get('coasting') is not False:
                    raise ValueError('invalid selected person')
                # This rehearsal tracker preserves detection boxes exactly; never accept an invented box.
                values=[t.get(k) for k in ('x','y','w','h','conf')]
                if not all(type(v) in (int,float) and math.isfinite(v) for v in values):raise ValueError('nonfinite person')
                x,y,w,h,conf=values
                if x<0 or y<0 or w<=0 or h<=0 or x+w>1280 or y+h>720 or not 0<=conf<=1:raise ValueError('invalid person bounds')
                if not any(r.get('label')=='person' and abs(conf-r['confidence'])<=1e-6 and all(abs(t[k]-r[k])<=1e-6 for k in ('x','y','w','h')) for r in previous[1]):
                    raise ValueError('selected box is not from this inference')
            key=(frame['epoch'],generation,None if t is None else t['id'])
            if key!=self.key:self.follower.reset();self.key=key
            # Producer-reported PC age and local elapsed are conservative, never cross-clock subtraction.
            out=self.follower.step(dict(obs,age_ms=age+elapsed*1000),self.clock())
            if t is None and obs.get('selected_id') is not None:
                out.update(reason='selected person not seen in this frame',target_id=obs['selected_id'])
            return dict(out,selection_gen=generation,frame_seq=frame['seq'],frame_epoch=frame['epoch'],instance=self.instance,
                        limits=limits(),transmit=False,request_id=previous[0])
        except Exception:
            self.follower.reset();self.key=None
            raise
