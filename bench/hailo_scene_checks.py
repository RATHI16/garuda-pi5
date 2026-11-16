"""Prospective criteria for moving-person scenes, independent of the flight runner."""
import math
from hailo_sitl_checks import LIMITS,separation,speed

def score_scene(rows,errors,scene):
 motion=[r for r in rows if r['test_stage']=='motion']
 checks=dict(airborne_motion=bool(motion) and all(r.get('armed') is True and r.get('phase')=='FLYING' for r in motion),no_run_errors=not errors,observed=bool(motion) and len(motion)>=100,
  landed_disarmed=bool(rows) and rows[-1].get('landed') is True and rows[-1].get('armed') is False,
  speed_bound=bool(rows) and all(speed(r)<=LIMITS['max_speed_mps'] for r in rows))
 measures={}
 try:
  checks['truth_fresh']=bool(rows) and all(0<=r['truth']['age_s']<=.5 for r in rows)
  dist=[separation(r) for r in rows];measures['minimum_separation_m']=min(dist)
  checks['minimum_separation']=min(dist)>=LIMITS['minimum_separation_m']
  a=motion[0]['truth']['walker']['position'];b=motion[-1]['truth']['walker']['position']
  measures['actor_displacement_m']=math.dist(a[:2],b[:2]);checks['actor_moved']=measures['actor_displacement_m']>=3
  checks['complete_route']=motion[0]['truth']['sim_ns']<=121e9 and motion[-1]['truth']['sim_ns']>=160e9
  measures['locked_fraction']=sum(r.get('lock_state')=='LOCKED' for r in motion)/len(motion)
  identities={(r.get('frame',{}).get('epoch'),r.get('target_id')) for r in motion if r.get('lock_state')=='LOCKED'}
  checks['no_target_switch']=len(identities)==1 and all(e and i is not None for e,i in identities)
  if scene in ('walk-turn','walls-follow'):
   checks['locked']=measures['locked_fraction']>=LIMITS['locked_fraction']
   checks['convergence']=all(abs(separation(r)-3)<=LIMITS['convergence_m'] for r in motion[-5:])
  if scene=='walls-follow':
   walls=[(7,-3.5,20,1),(7,3.5,20,1),(2,-11.5,1,10),(17,4.5,1,17)]
   clearance=[]
   for r in rows:
    x,y=r['truth']['garuda_x500']['position'][:2]
    clearance.extend(math.hypot(max(abs(x-cx)-w/2,0),max(abs(y-cy)-h/2,0))-.35 for cx,cy,w,h in walls)
   measures['minimum_wall_clearance_m']=min(clearance);checks['wall_clearance']=min(clearance)>=.5
   depths=[t['range_m'] for r in motion for t in r.get('tracks',[]) if t.get('id')==r.get('target_id') and isinstance(t.get('range_m'),(int,float)) and math.isfinite(t['range_m'])]
   measures['reported_person_depth_m']={'min':min(depths),'max':max(depths)} if depths else None
   measures['final_horizontal_separation_m']=separation(motion[-1])
  if scene=='screen-crossing':
   lost=next((i for i,r in enumerate(motion) if r.get('requires_reselection')),None)
   checks['reselection_latched']=lost is not None
   measures['visual_loss_samples']=sum(r.get('lock_state')=='LOST' for r in motion)
   checks['latched_hold']=lost is not None and len(motion[lost:])>=5 and all(speed(r)<1e-9 and r.get('requires_reselection') for r in motion[lost:])
   # Distance to screen's horizontal AABB, minus a conservative vehicle radius.
   clearance=[math.hypot(max(7.8-r['truth']['garuda_x500']['position'][0],0,r['truth']['garuda_x500']['position'][0]-8.2),max(1-r['truth']['garuda_x500']['position'][1],0,r['truth']['garuda_x500']['position'][1]-3))-.35 for r in rows]
   measures['minimum_screen_clearance_m']=min(clearance);checks['screen_clearance']=min(clearance)>.2
 except (KeyError,TypeError,IndexError,ValueError,ZeroDivisionError):checks['complete_truth']=False
 return dict(passed=all(checks.values()),checks=checks,measurements=measures,errors=errors,limits=LIMITS)
