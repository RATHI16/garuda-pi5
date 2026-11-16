"""Predeclared stationary-target simulation checks; truth is scoring-only."""
import math

LIMITS=dict(stable_frames=5,edge_margin=.05,confidence=.5,max_age_ms=700,
            follow_seconds=15,minimum_follow_samples=50,locked_fraction=.9,
            convergence_m=.75,minimum_separation_m=2.,hold_deadline_s=1.,
            minimum_hold_samples=5,max_speed_mps=1.01,moving_fault_floor=.2,max_fault_travel_m=.75)

class StableTarget:
 def __init__(self):self.key=None;self.seq=None;self.count=0
 def update(self,s):
  f=s.get('frame',{});candidates=[]
  age=s.get('perception_age_ms');width=f.get('width',0);height=f.get('height',0)
  if isinstance(age,(int,float)) and 0<=age<LIMITS['max_age_ms'] and width>0 and height>0:
   for t in s.get('tracks',[]):
    if (t.get('label')=='person' and not t.get('coasting') and t.get('conf',0)>=LIMITS['confidence']
        and isinstance(t.get('range_m'),(int,float)) and math.isfinite(t['range_m']) and .3<t['range_m']<30
        and t['x']>=width*LIMITS['edge_margin'] and t['y']>=height*LIMITS['edge_margin']
        and t['x']+t['w']<=width*(1-LIMITS['edge_margin']) and t['y']+t['h']<=height*(1-LIMITS['edge_margin'])):candidates.append(t)
  if len(candidates)!=1:self.key=None;self.seq=None;self.count=0;return None
  t=candidates[0];key=(f.get('epoch'),t['id']);seq=f.get('seq')
  if not key[0] or type(seq) is not int:return None
  if key!=self.key or (self.seq is not None and seq<self.seq):self.key=key;self.seq=None;self.count=0
  if seq!=self.seq:self.count+=1;self.seq=seq
  return t if self.count>=LIMITS['stable_frames'] else None

def speed(r):return math.hypot(*r['intent'][:2])
def separation(r):
 t=r['truth'];a=t['walker']['position'];d=t['garuda_x500']['position']
 return math.hypot(a[0]-d[0],a[1]-d[1])

def score(rows,errors):
 follow=[r for r in rows if r['test_stage']=='follow'];drop=[r for r in rows if r['test_stage']=='dropout']
 recovery=[r for r in rows if r['test_stage']=='recovery_hold'];resumed=[r for r in rows if r['test_stage']=='resumed']
 checks={};measurements={}
 checks['no_run_errors']=not errors
 checks['follow_observed']=len(follow)>=LIMITS['minimum_follow_samples'] and bool(follow) and follow[-1]['wall_monotonic']-follow[0]['wall_monotonic']>=14
 identities={(r.get('detector',{}).get('sha256'),r.get('metrics',{}).get('remote',{}).get('instance')) for r in follow+resumed}
 checks['detector_identity']=len(identities)==1 and all(all(isinstance(v,str) and v for v in identity) for identity in identities)
 checks['target_identity']=all(len({(r.get('frame',{}).get('epoch'),r.get('target_id')) for r in group})==1 and all(r.get('target_id') is not None and r.get('frame',{}).get('epoch') for r in group) for group in (follow,resumed))
 checks['locked']=bool(follow) and sum(r.get('lock_state')=='LOCKED' for r in follow)/len(follow)>=LIMITS['locked_fraction']
 closing=[r for r in rows if r['test_stage']=='closing'];moving_drop=[r for r in rows if r['test_stage']=='moving_dropout'];moving_recovery=[r for r in rows if r['test_stage']=='moving_recovery_hold']
 checks['moving_before_fault']=bool(closing) and speed(closing[-1])>=LIMITS['moving_fault_floor'] and closing[-1].get('lock_state')=='LOCKED'
 moving_settled=[r for r in moving_drop if r['wall_monotonic']-moving_drop[0]['wall_monotonic']>=LIMITS['hold_deadline_s']]
 checks['moving_dropout_hold']=checks['moving_before_fault'] and len(moving_settled)>=LIMITS['minimum_hold_samples'] and all(speed(r)<1e-9 and r.get('requires_reselection') for r in moving_settled)
 checks['moving_no_implicit_resume']=len(moving_recovery)>=5 and all(speed(r)<1e-9 and r.get('requires_reselection') for r in moving_recovery)
 settled=[r for r in drop if r['wall_monotonic']-drop[0]['wall_monotonic']>=LIMITS['hold_deadline_s']]
 checks['dropout_hold']=checks['locked'] and checks['moving_before_fault'] and len(settled)>=LIMITS['minimum_hold_samples'] and all(speed(r)<1e-9 and r.get('requires_reselection') for r in settled)
 checks['no_implicit_resume']=len(recovery)>=5 and all(speed(r)<1e-9 and r.get('requires_reselection') for r in recovery)
 checks['explicit_recovery']=len(resumed)>=5 and all(not r.get('requires_reselection',True) and r.get('lock_state')=='LOCKED' and r.get('behavior')=='follow' for r in resumed)
 checks['landed_disarmed']=bool(rows) and rows[-1].get('landed') is True and rows[-1].get('armed') is False
 checks['speed_bound']=bool(rows) and all(speed(r)<=LIMITS['max_speed_mps'] for r in rows)
 try:
  distances=[separation(r) for r in follow]
  origin=closing[-1]['truth']['garuda_x500']['position']
  travel=[math.hypot(r['truth']['garuda_x500']['position'][0]-origin[0],r['truth']['garuda_x500']['position'][1]-origin[1]) for r in moving_drop]
  checks['moving_stop_travel']=bool(travel) and max(travel)<=LIMITS['max_fault_travel_m']
  measurements['moving_fault_travel_m']=max(travel) if travel else None
  zero=[r for r in moving_drop if speed(r)<1e-9 and r.get('requires_reselection')]
  measurements['moving_time_to_hold_s']=zero[0]['wall_monotonic']-closing[-1]['wall_monotonic'] if zero else None
  delay=measurements['moving_time_to_hold_s']
  checks['moving_hold_deadline']=delay is not None and 0<=delay<=LIMITS['hold_deadline_s']
  pairs=[]
  for r in follow:
   target=next((t for t in r.get('tracks',[]) if t.get('id')==r.get('target_id')),None)
   if target and target.get('range_m') is not None:pairs.append(dict(depth_range_m=target['range_m'],truth_horizontal_m=separation(r)))
  measurements['depth_vs_horizontal_truth']=pairs
  delivery={}
  for stage in {r['test_stage'] for r in rows}:
   group=[r for r in rows if r['test_stage']==stage]
   if all('delivery' in r for r in (group[0],group[-1])):delivery[stage]={k:group[-1]['delivery'][k]-group[0]['delivery'][k] for k in ('reader_frames','controller_frames','evaluated_frames','queue_overwrites')}
  measurements['delivery_deltas']=delivery
  checks['truth_fresh']=bool(rows) and all(0<=r['truth']['age_s']<=.5 for r in rows)
  checks['minimum_separation']=bool(distances) and min(separation(r) for r in rows)>=LIMITS['minimum_separation_m']
  checks['convergence']=len(distances)>=5 and all(abs(d-3)<=LIMITS['convergence_m'] for d in distances[-5:])
  measurements.update(final_separation_m=distances[-1] if distances else None,minimum_separation_m=min(distances) if distances else None)
 except (KeyError,TypeError,ValueError,IndexError):checks.update(truth_fresh=False,minimum_separation=False,convergence=False,moving_stop_travel=False)
 return dict(passed=all(checks.values()),checks=checks,measurements=measurements,limits=LIMITS,errors=errors)
