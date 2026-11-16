from hailo_sitl_checks import StableTarget,score

def observation(seq):
 return dict(perception_age_ms=10,frame=dict(seq=seq,epoch='a',width=1280,height=720),tracks=[dict(label='person',id=1,x=500,y=200,w=100,h=300,conf=.8,range_m=5)])
def test_selection_needs_distinct_frames_and_margin():
 select=StableTarget()
 for _ in range(10):assert select.update(observation(1)) is None
 for i in range(2,5):assert select.update(observation(i)) is None
 assert select.update(observation(5))['id']==1
 edge=observation(6);edge['tracks'][0]['x']=0
 assert select.update(edge) is None
 assert select.update(observation(7)) is None

def test_no_follow_cannot_pass_dropout():
 result=score([],[])
 assert not result['passed'] and not result['checks']['dropout_hold']

def row(stage,t,moving=False):
 return dict(detector=dict(sha256='model'),metrics=dict(remote=dict(instance='npu')),frame=dict(epoch='camera'),target_id=1,test_stage=stage,wall_monotonic=t,intent=[.1 if moving else 0,0],lock_state='LOCKED',behavior='follow',requires_reselection=stage in ('dropout','recovery_hold','moving_dropout','moving_recovery_hold'),armed=stage!='land',landed=stage=='land',truth=dict(age_s=.01,walker=dict(position=[6,0,1]),garuda_x500=dict(position=[3,0,1])))
def valid():
 closing=row('closing',0);closing['intent']=[.4,0]
 return [closing]+[row('moving_dropout',.2+i*.2) for i in range(15)]+[row('moving_recovery_hold',3.2+i*.2) for i in range(10)]+[row('follow',5.2+i*.2,i<10) for i in range(75)]+[row('dropout',20.2+i*.2) for i in range(15)]+[row('recovery_hold',23.2+i*.2) for i in range(10)]+[row('resumed',25.2+i*.2) for i in range(15)]+[row('land',29)]
def test_complete_evidence_and_recovery_required():
 rows=valid();assert score(rows,[])['passed']
 for stage in ['follow','dropout','recovery_hold','resumed','land']:
  assert not score([r for r in rows if r['test_stage']!=stage],[])['passed']
 assert not score(rows,['failure'])['passed']

def test_stale_truth_frozen_follow_and_implicit_resume_fail():
 rows=valid();rows[0]['truth']['age_s']=2;assert not score(rows,[])['passed']
 rows=valid()
 for r in rows:r['intent']=[0,0]
 assert not score(rows,[])['checks']['dropout_hold']
 rows=valid();next(r for r in rows if r['test_stage']=='recovery_hold')['requires_reselection']=False
 assert not score(rows,[])['passed']

def test_identity_changes_cannot_pass():
 rows=valid();next(r for r in rows if r['test_stage']=='follow')['metrics']['remote']['instance']='other'
 assert not score(rows,[])['passed']
 rows=valid();next(r for r in rows if r['test_stage']=='follow')['target_id']=2
 assert not score(rows,[])['passed']

def test_selection_waits_for_hold_then_follow_ack():
 from hailo_sitl_smoke import select_then_follow
 events=[]
 def send(**cmd):events.append(cmd['command'])
 def until(predicate,stage,timeout):
  if stage.endswith('_selected'):
   assert events==['select']
   assert not predicate(dict(target_id=1,requires_reselection=True,behavior='follow'))
   assert predicate(dict(target_id=1,requires_reselection=False,behavior='hold'))
  else:
   assert events==['select','follow']
   assert not predicate(dict(target_id=1,requires_reselection=False,behavior='hold',lock_state='LOCKED'))
   assert predicate(dict(target_id=1,requires_reselection=False,behavior='follow',lock_state='LOCKED'))
 select_then_follow(send,until,1,'camera','reselect')

def test_moving_fault_requires_motion_and_bounded_travel():
 rows=valid();rows[0]['intent']=[0,0];assert not score(rows,[])['passed']
 rows=valid();rows[1]['truth']['garuda_x500']['position']=[4,0,1]
 assert not score(rows,[])['checks']['moving_stop_travel']

def test_running_world_identity(tmp_path):
 import os,json,hashlib
 from pathlib import Path
 from hailo_sitl_smoke import check_world
 p=tmp_path/'world.sdf'
 p.write_text("<sdf><world><actor><script><trajectory><waypoint><time>0</time><pose>6 0 1 0 0 0</pose></waypoint><waypoint><time>600</time><pose>6 0 1 0 0 0</pose></waypoint></trajectory></script></actor><model name='garuda_x500'><link name='follow_camera'><pose>0.15 0 0.18 0 0.3 0</pose></link></model></world></sdf>")
 meta=dict(pid=os.getpid(),start_ticks=Path(f'/proc/{os.getpid()}/stat').read_text().split(') ',1)[1].split()[19],boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),partition='garuda_isolated',world=str(p),world_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 m=p.with_name('world-instance.json');m.write_text(json.dumps(meta));assert check_world(p)==meta['world_sha256']
 meta['start_ticks']='old';m.write_text(json.dumps(meta))
 import pytest
 with pytest.raises(RuntimeError):check_world(p)

def test_truth_failure_does_not_skip_landing_confirmation(tmp_path,monkeypatch):
 import pytest,json
 import hailo_sitl_smoke as h
 import sitl_truth
 monkeypatch.setenv('GARUDA_SIM','1');monkeypatch.setenv('GZ_PARTITION','garuda_isolated')
 monkeypatch.setattr(h,'__file__',str(tmp_path/'bench'/'harness.py'))
 monkeypatch.setattr(h,'check_world',lambda p:'hash')
 tick=[0.];armed=[False];calls=[];seq=[0]
 def clock():tick[0]+=.05;return tick[0]
 monkeypatch.setattr(h.time,'monotonic',clock);monkeypatch.setattr(h.time,'sleep',lambda t:None)
 class FakeTruth:
  n=0
  def snapshot(self):
   self.n+=1
   if self.n>1:raise RuntimeError('recorder failed')
   return dict(age_s=0,walker=dict(position=[6,0,1]),garuda_x500=dict(position=[0,0,0],quaternion=[1,0,0,0]))
 monkeypatch.setattr(sitl_truth,'Truth',FakeTruth)
 mode=['hold'];selected=[None]
 def send(**cmd):
  calls.append(cmd['command'])
  if cmd['command']=='takeoff':armed[0]=True
  if cmd['command']=='land':armed[0]=False
  if cmd['command']=='select':selected[0]=1;mode[0]='hold'
  if cmd['command']=='follow':mode[0]='follow'
 def state():
  seq[0]+=1;s=observation(seq[0]);s.update(phase='FLYING' if armed[0] else 'GROUND',armed=armed[0],landed=not armed[0],telemetry_fresh=True,altitude=1.5,detector=dict(engine='hailo10h-rpc'),camera=dict(pitch=.3),target_id=selected[0],requires_reselection=False,behavior=mode[0],lock_state='LOCKED',intent=[.4,0]);s['frame']['sim_ns']=1;return s
 monkeypatch.setattr(h,'state',state);monkeypatch.setattr(h,'command',send);monkeypatch.setattr(h,'pause',lambda v:None)
 with pytest.raises(SystemExit):h.main()
 assert calls[-1]=='land'
 logs=list((tmp_path/'.runtime').glob('*.jsonl'));rows=[json.loads(l) for l in logs[0].read_text().splitlines()]
 assert rows[-1]['test_stage']=='land' and rows[-1]['landed'] and rows[-1]['truth'] is None

def test_late_hold_fails_even_if_later_samples_are_zero():
 rows=valid()
 for r in rows:
  if r['test_stage']=='moving_dropout' and r['wall_monotonic']<1.1:r['intent']=[.4,0]
 assert not score(rows,[])['checks']['moving_hold_deadline']
