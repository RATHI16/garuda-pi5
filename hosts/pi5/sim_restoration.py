"""Record prior service provenance and verify restoration; runs locally on the Pi."""
import argparse,datetime,hashlib,json,shlex,subprocess,threading,time,urllib.request
from pathlib import Path
UNIT='garuda-mono.service'
TRIAL='garuda-sim-detector.service'

def write_evidence(path,value):
 with path.open('x') as f:json.dump(value,f,indent=2)

def unit_state(unit=UNIT):
 cmd=['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','NRestarts','-p','Environment','-p','EnvironmentFiles','-p','LoadState']
 result=subprocess.run(cmd,text=True,timeout=3,capture_output=True)
 state=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
 # --collect removes finished transient units; an explicit not-found state is evidence.
 if result.returncode and not (unit==TRIAL and state.get('LoadState')=='not-found' and state.get('ActiveState')=='inactive' and state.get('MainPID')=='0'):
  raise RuntimeError('systemctl show failed: '+result.stderr)
 return state

def prior_mode(state):
 mode=state.get('ActiveState')
 if mode not in ('active','inactive','failed'):raise ValueError('unstable or unknown prior service state')
 return mode

def score(prior,rows):
 if not rows:return dict(restored=False,reason='no evidence')
 if prior not in ('active','inactive','failed'):return dict(restored=False,reason='unknown prior state')
 trial_ended=all(r.get('trial',{}).get('ActiveState') in ('inactive','failed') and r.get('trial',{}).get('MainPID')=='0' for r in rows)
 if not trial_ended:return dict(restored=False,trial_ended=False,inference_verified=False,reason='trial not confirmed ended')
 if prior!='active':
  good=all(r.get('unit',{}).get('ActiveState') in ('inactive','failed') for r in rows)
  return dict(restored=good,reason='originally stopped; no inference requested',inference_verified=False)
 units=[r.get('unit',{}) for r in rows]
 stable=all(u.get('ActiveState')=='active' and str(u.get('MainPID','')).isdigit() and int(u.get('MainPID','0'))>0 and str(u.get('NRestarts','')).isdigit() for u in units) and len({u.get('MainPID') for u in units})==1 and len({u.get('NRestarts') for u in units})==1
 frames=[r.get('state',{}).get('frame',{}) for r in rows]
 epochs={f.get('epoch') for f in frames};seqs=[f.get('seq') for f in frames]
 progress=len(epochs)==1 and None not in epochs and all(type(s) is int for s in seqs) and all(b>=a for a,b in zip(seqs,seqs[1:])) and len(set(seqs))>=2
 fresh=all(isinstance(r.get('state',{}).get('age_ms'),(int,float)) and 0<=r['state']['age_ms']<1000 for r in rows)
 stats=[r.get('state',{}).get('stats',{}) for r in rows]
 instances={v.get('instance') for v in stats};counts=[v.get('inferences') for v in stats]
 inferred=len(instances)==1 and all(isinstance(i,str) and i for i in instances) and all(type(n) is int and n>=0 for n in counts) and all(b>=a for a,b in zip(counts,counts[1:])) and len(set(counts))>=2
 return dict(trial_ended=True,inference_counter_progress=inferred,restored=bool(stable and progress and fresh and inferred),stable_process=stable,processed_frame_progress=progress,fresh=fresh,inference_verified=bool(stable and progress and fresh and inferred))

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prior','verify']);p.add_argument('--out',type=Path,required=True);p.add_argument('--prior',type=Path);p.add_argument('--seconds',type=int,default=6);a=p.parse_args()
 if not 2<=a.seconds<=30:p.error('seconds must be 2..30')
 if a.out.exists():p.error('output already exists; use a new --out for each attempt')
 if a.action=='prior':
  state=unit_state();mode=prior_mode(state)
  env=dict(x.split('=',1) for x in shlex.split(state.get('Environment','')) if '=' in x)
  hef=Path(env.get('GARUDA_MONO_YOLO','/home/garuda/garuda-sdk-deploy/models/yolo26m.hef'))
  wrapper=Path('/home/garuda/garuda-sdk-deploy/hosts/pi5/hailo_yolo26.py')
  value=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),unit_name=UNIT,unit=state,prior_mode=mode,
    environment_note='EnvironmentFiles are recorded but not resolved; Environment/default values may not describe effective settings',
    detector=dict(hef=str(hef),sha256=hashlib.sha256(hef.read_bytes()).hexdigest(),wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),confidence=env.get('GARUDA_MONO_CONF','0.15')))
  write_evidence(a.out,value);print(mode);return
 if a.prior is None:p.error('--prior required for verify')
 prior=json.loads(a.prior.read_text());mode=prior['prior_mode'];stop=threading.Event();rows=[];error=None;viewer_status=dict(connected=False,error=None)
 def viewer():
  try:
   with urllib.request.urlopen('http://127.0.0.1:8098/stream',timeout=2) as r:
    viewer_status["connected"]=True
    while not stop.is_set():
     if not r.read(8192):break
  except Exception as e:viewer_status["error"]=str(e)
 thread=None
 try:
  if mode=='active':
   thread=threading.Thread(target=viewer,daemon=True);thread.start();time.sleep(2)
  for _ in range(a.seconds):
   row=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),unit_name=UNIT)
   try:
    row['trial']=unit_state(TRIAL)
    row['unit']=unit_state()
    if mode=='active':
     with urllib.request.urlopen('http://127.0.0.1:8098/api/state',timeout=2) as r:s=json.load(r)
     row['state']={k:s.get(k) for k in ['frame','age_ms','stats','detector']}
   except Exception as e:row['error']=str(e)
   rows.append(row);time.sleep(1)
 except Exception as e:error=str(e)
 finally:
  stop.set()
  if thread:thread.join(timeout=3)
  result=score(mode,rows)
  if error:result.update(restored=False,error=error)
  write_evidence(a.out,dict(prior=str(a.prior),viewer=viewer_status,commands=['systemctl show '+TRIAL,'systemctl show '+UNIT,'GET /stream (only if previously active)','GET /api/state (only if previously active)'],rows=rows,result=result))
 print(json.dumps(result))
 if not result['restored']:raise SystemExit(1)

if __name__=='__main__':main()
