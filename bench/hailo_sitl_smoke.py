"""Stationary Gazebo->Pi Hailo->PX4 check; no physical depth qualification."""
import hashlib,json,math,os,time,uuid
from pathlib import Path
import xml.etree.ElementTree as ET
from visual_sitl_smoke import state,command,pause
from hailo_sitl_checks import StableTarget,LIMITS,score,separation

def check_world(path, expected_scene=None):
 data=path.read_bytes();tree=ET.fromstring(data)
 points=tree.findall('.//actor/script/trajectory/waypoint')
 if expected_scene is not None:
  if ET.tostring(tree)!=ET.tostring(ET.fromstring(expected_scene)):raise RuntimeError('bench scene differs from declared scenario')
 elif len(points)!=2 or [p.findtext('time') for p in points]!=['0','600'] or any(p.findtext('pose')!='6 0 1 0 0 0' for p in points):
  raise RuntimeError('requires fresh start_sitl.py --visual --stationary-check world')
 if tree.findtext(".//model[@name='garuda_x500']/link[@name='follow_camera']/pose")!='0.15 0 0.18 0 0.3 0':raise RuntimeError('requires stationary-v2 camera geometry')
 digest=hashlib.sha256(data).hexdigest()
 instance=json.loads(path.with_name('world-instance.json').read_text())
 if instance['world_sha256']!=digest or instance['partition']!='garuda_isolated' or instance['world']!=str(path):raise RuntimeError('world instance mismatch')
 if instance['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():raise RuntimeError('old world boot')
 stat=Path(f"/proc/{instance['pid']}/stat").read_text().split(') ',1)[1].split()
 if stat[19]!=instance['start_ticks'] or stat[0]=='Z':raise RuntimeError('world process ended or replaced')
 return digest

def select_then_follow(send,until,target_id,epoch,stage):
 send(command='select',id=target_id,epoch=epoch)
 until(lambda s:s['target_id']==target_id and not s['requires_reselection'] and s['behavior']=='hold',stage+'_selected',3)
 send(command='follow')
 until(lambda s:s['target_id']==target_id and not s['requires_reselection'] and s['behavior']=='follow' and s['lock_state']=='LOCKED',stage+'_follow_confirmed',3)

def main():
 if os.environ.get('GARUDA_SIM')!='1' or os.environ.get('GZ_PARTITION')!='garuda_isolated':raise SystemExit('isolated GARUDA_SIM and GZ_PARTITION required')
 from sitl_truth import Truth
 truth=Truth();rows=[];errors=[]
 root=Path(__file__).resolve().parents[1];folder=root/'.runtime';folder.mkdir(exist_ok=True)
 path=folder/('hailo-flight-'+uuid.uuid4().hex+'.jsonl')
 manifest=dict(limits=LIMITS,scenario='stationary-v2-camera0.3',scope='Pi Hailo detection; PC tracking; Gazebo RGBD depth',world_sha256=None)
 with path.open('x') as log:
  def sample(stage):
   s=state();s.update(test_stage=stage,wall_monotonic=time.monotonic())
   try:s['truth']=truth.snapshot()
   except Exception as e:s.update(truth=None,truth_error=str(e))
   rows.append(s);log.write(json.dumps(s,allow_nan=False)+'\n');log.flush();return s
  def until(fn,stage,seconds):
   end=time.monotonic()+seconds
   while time.monotonic()<end:
    s=sample(stage)
    if fn(s):return s
    time.sleep(.2)
   raise RuntimeError(stage+' timeout')
  def period(stage,seconds):
   end=time.monotonic()+seconds
   while time.monotonic()<end:sample(stage);time.sleep(.2)
  def choose(stage):
   selector=StableTarget();chosen=[]
   def ready(s):
    t=selector.update(s)
    if t:chosen.append(t)
    return t is not None
   s=until(ready,stage,20)
   select_then_follow(command,until,chosen[-1]['id'],s['frame']['epoch'],stage)
  attempted=False
  try:
   manifest['world_sha256']=check_world(folder/'sitl/garuda_lab.sdf')
   time.sleep(1)
   s=sample('preflight')
   if s['armed'] or not s['landed'] or not s['telemetry_fresh']:raise RuntimeError('requires grounded simulator with telemetry')
   t=s['truth'];d=t['garuda_x500'];a=t['walker'];q=d['quaternion'];yaw=math.atan2(2*(q[0]*q[3]+q[1]*q[2]),1-2*(q[2]**2+q[3]**2))
   if math.hypot(*d['position'][:2])>.25 or abs(yaw)>.1 or math.hypot(a['position'][0]-6,a['position'][1])>.1:raise RuntimeError('fixed start pose not met')
   command(command='source',kind='gazebo');command(command='gestures',enabled=False);command(command='settings',gap=3,speed=1,limit=15)
   s=until(lambda s:s.get('detector',{}).get('engine')=='hailo10h-rpc' and 0<=s.get('perception_age_ms',1e9)<700,'camera',15)
   if s.get('camera',{}).get('pitch')!=.3:raise RuntimeError('vision camera geometry differs from scenario')
   if not 0<s['frame'].get('sim_ns',0)<120e9:raise RuntimeError('requires fresh stationary script phase (<120 sim seconds)')
   manifest['detector']=s['detector'];manifest['start_sim_ns']=s['frame']['sim_ns']
   attempted=True;command(command='takeoff')
   until(lambda s:s['phase']=='FLYING' and s['altitude']>1.2,'takeoff',30)
   choose('target')
   until(lambda s:separation(s)<=4.5 and math.hypot(*s['intent'][:2])>=LIMITS['moving_fault_floor'],'closing',12)
   pause(True);period('moving_dropout',3);pause(False);period('moving_recovery_hold',2)
   choose('moving_reselect');period('follow',LIMITS['follow_seconds'])
   # Refuse vacuous fault injection if the preceding follow failed.
   follow=[r for r in rows if r['test_stage']=='follow']
   if sum(r['lock_state']=='LOCKED' for r in follow)/len(follow)<.9:raise RuntimeError('follow not established before fault')
   pause(True);period('dropout',3);pause(False);period('recovery_hold',2)
   choose('reselect');period('resumed',3);command(command='hold')
  except Exception as e:errors.append(str(e))
  finally:
   # Independent cleanup attempts: a failed unpause must not prevent landing.
   if attempted:
    try:pause(False)
    except Exception as e:errors.append('unpause: '+str(e))
    try:
     command(command='land')
     until(lambda s:not s['armed'] and s['landed'],'land',45)
    except Exception as e:errors.append('landing: '+str(e))
   try:result=score(rows,errors)
   except Exception as e:result=dict(passed=False,errors=errors+['scoring: '+str(e)],limits=LIMITS)
   result.update(manifest=manifest,log=str(path))
   with path.with_suffix('.summary.json').open('x') as out:json.dump(result,out,indent=2,allow_nan=False)
   print(json.dumps(result))
 if not result['passed']:raise SystemExit('checks failed; inspect preserved summary')
if __name__=='__main__':main()
