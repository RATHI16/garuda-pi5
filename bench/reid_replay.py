#!/usr/bin/env python3
"""Replay an identity recording (hosts/pi5/mono_record.py) through the identity owner.

Offline check of the re-identification rules on recorded numbers; not an accuracy claim.
Reports every bind, how long the selected person was visible but unbound, and how the
decision states were distributed."""
import argparse,collections,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'hosts/pi5'))
from mono_identity import IdentityOwner

def replay(rows,select=None,**params):
    owner=IdentityOwner(**params);t0=rows[0]['t'];binds=[];states=collections.Counter();unbound=0.;last=None
    first=select if select is not None else rows[0]['lock']
    owner.select(first,0.)
    for r in rows:
        now=r['t']-t0
        people=[dict(id=t['id'],emb=r['emb'].get(str(t['id'])),pos=None) for t in r['tracks'] if t['label']=='person' and not t['coasting']]
        d=owner.update(now,people);states[d['state']]+=1
        if d['rebound_from'] is not None and (not binds or binds[-1]['to']!=d['target']):
            binds.append(dict(t=round(now,2),**{'from':d['rebound_from']},to=d['target'],score=d['rebound_score']))
        visible_people=bool(people);bound_visible=any(p['id']==d['target'] for p in people)
        if last is not None and visible_people and not bound_visible:unbound+=now-last
        last=now
    return dict(frames=len(rows),seconds=round(rows[-1]['t']-t0,1),selected=first,binds=binds,final_target=owner.tid,gallery=len(owner.gallery),
        states=dict(states),seconds_someone_visible_but_not_bound=round(unbound,2))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('recording',type=Path);p.add_argument('--select',type=int)
    p.add_argument('--bind-score',type=float,default=.80);p.add_argument('--margin',type=float,default=.10);a=p.parse_args()
    rows=[json.loads(l) for l in a.recording.open()]
    print(json.dumps(replay(rows,a.select,bind_score=a.bind_score,margin=a.margin),indent=1))
if __name__=='__main__':main()
