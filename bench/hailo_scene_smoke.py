"""Visible moving-person bench runs. Uses real Hailo detection, simulated RGBD."""
import argparse,json,os,sys,time,uuid,signal
from pathlib import Path
from xml.sax.saxutils import escape
from visual_sitl_smoke import state,command,pause
from hailo_sitl_checks import StableTarget
from hailo_sitl_smoke import check_world,select_then_follow
from hailo_scene_checks import score_scene
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hosts/pc'))
from bench_scenes import NAMES,build_scene

def main():
 p=argparse.ArgumentParser();p.add_argument('scene',choices=NAMES);p.add_argument('--actor',required=True);p.add_argument('--walls-world');a=p.parse_args()
 if os.environ.get('GARUDA_SIM')!='1' or os.environ.get('GZ_PARTITION')!='garuda_isolated':raise SystemExit('isolated simulation required')
 from sitl_truth import Truth
 truth=Truth();rows=[];errors=[];attempted=False
 def interrupted(signum,frame):raise RuntimeError('recorder interrupted by signal '+str(signum))
 signal.signal(signal.SIGTERM,interrupted)
 path=ROOT/'.runtime'/('hailo-scene-'+a.scene+'-'+uuid.uuid4().hex+'.jsonl')
 manifest=dict(scene=a.scene,scope='Pi Hailo detection, PC tracking, Gazebo depth; no obstacle avoidance')
 with path.open('x') as log:
  def sample(stage):
   s=state();s.update(test_stage=stage,wall_monotonic=time.monotonic())
   try:s['truth']=truth.snapshot()
   except Exception as e:s.update(truth=None,truth_error=str(e))
   rows.append(s);log.write(json.dumps(s,allow_nan=False)+'\n');log.flush();return s
  def until(fn,stage,seconds):
   deadline=time.monotonic()+seconds
   while time.monotonic()<deadline:
    s=sample(stage)
    if fn(s):return s
    time.sleep(.2)
   raise RuntimeError(stage+' timeout')
  try:
   expected=build_scene((ROOT/'hosts/pc/gazebo_lab.sdf').read_text().replace('@ACTOR_MESH@',escape(str(Path(a.actor).resolve()))),a.scene,**({'walls_world':a.walls_world} if a.scene=='walls-follow' else {}))
   manifest['world_sha256']=check_world(ROOT/'.runtime/sitl/garuda_lab.sdf',expected)
   deadline=time.monotonic()+10
   while True:
    try:truth.snapshot();break
    except RuntimeError:
     if time.monotonic()>=deadline:raise
     time.sleep(.05)
   s=until(lambda s:s['telemetry_fresh'],'preflight',20)
   if s['armed'] or not s['landed']:raise RuntimeError('requires grounded/disarmed simulator')
   command(command='source',kind='gazebo');command(command='gestures',enabled=False);command(command='settings',gap=3,speed=1,limit=15)
   s=until(lambda s:s.get('detector',{}).get('engine')=='hailo10h-rpc' and 0<=s.get('perception_age_ms',1e9)<700,'camera',20)
   if s['camera'].get('pitch')!=.3 or not 0<s['frame'].get('sim_ns',0)<60e9:raise RuntimeError('requires matching camera and scene start under 60 seconds')
   manifest['detector']=s['detector'];attempted=True;command(command='takeoff')
   until(lambda s:s['phase']=='FLYING' and s['altitude']>1.2,'takeoff',30)
   selector=StableTarget();chosen=[]
   def ready(s):
    t=selector.update(s)
    if t:chosen.append(t)
    return t is not None
   s=until(ready,'target',20)
   select_then_follow(command,until,chosen[-1]['id'],s['frame']['epoch'],'target')
   # Scheduling uses image simulation time, never actor truth to steer or select.
   until(lambda s:s['frame'].get('sim_ns',0)>=120e9,'settle',130)
   until(lambda s:s['frame'].get('sim_ns',0)>=165e9,'motion',65)
   command(command='hold')
  except Exception as e:errors.append(str(e))
  finally:
   signal.signal(signal.SIGTERM,signal.SIG_IGN)
   if attempted:
    try:pause(False)
    except Exception as e:errors.append('unpause: '+str(e))
    try:
     command(command='land');until(lambda s:not s['armed'] and s['landed'],'land',45)
    except Exception as e:errors.append('land: '+str(e))
   result=score_scene(rows,errors,a.scene);result.update(log=str(path),manifest=manifest)
   with path.with_suffix('.summary.json').open('x') as f:json.dump(result,f,indent=2)
   print(json.dumps(result),flush=True)
 if not result['passed']:raise SystemExit('scene checks failed; evidence preserved')
if __name__=='__main__':main()
