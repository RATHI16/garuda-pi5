#!/usr/bin/env python3
"""Collect bounded producer events and system/process resource samples. Read-only."""
import argparse,json,subprocess,threading,time,urllib.request
from pathlib import Path

def get(url):
 with urllib.request.urlopen(url,timeout=4) as r:return json.load(r)

def main():
 p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=60);p.add_argument('--out',required=True);p.add_argument('--viewer',action='store_true');a=p.parse_args()
 stop=threading.Event()
 def view():
  try:
   with urllib.request.urlopen('http://localhost:8098/stream',timeout=5) as r:
    while not stop.is_set():r.read(8192)
  except Exception:pass
 if a.viewer:threading.Thread(target=view,daemon=True).start()
 units=['garuda-oak-preview','garuda-mono','garuda-stereo-trial','garuda-mono-trial']
 pids={u:int(subprocess.check_output(['systemctl','show',u,'-p','MainPID','--value'],text=True).strip() or 0) for u in units}
 rows=[];events=[];cursor=-1;overruns=0
 for _ in range(a.seconds):
  start=time.monotonic();row={'host_ns':time.monotonic_ns()}
  for name,url in [('camera','http://localhost:8096/health'),('mono','http://localhost:8098/api/state')]:
   try:
    d=get(url);row[name]=d if name=='camera' else {k:d.get(k) for k in ['frame','stats','detector']}
   except Exception as e:row[name]={'error':str(e)}
  try:
   trace=get('http://localhost:8096/timing?after='+str(cursor));overruns+=int(trace['overrun']);events+=trace['records'];cursor=trace['next_id']-1
  except Exception as e:row['trace_error']=str(e)
  row['cpu']=Path('/proc/stat').read_text().split('intr')[0]
  row['processes']={}
  for unit,pid in pids.items():
   if not pid:continue
   try:row['processes'][unit]=dict(pid=pid,stat=Path(f'/proc/{pid}/stat').read_text(),status=Path(f'/proc/{pid}/status').read_text())
   except OSError:row['processes'][unit]={'error':'process ended'}
  row['thermal']=subprocess.check_output(['vcgencmd','measure_temp'],text=True).strip()
  row['throttle']=subprocess.check_output(['vcgencmd','get_throttled'],text=True).strip()
  rows.append(row)
  if int(row['throttle'].split('=')[1],16)&15:
   row['stop_reason']='active_throttle_flags';break
  time.sleep(max(0,1-(time.monotonic()-start)))
 stop.set();Path(a.out).write_text(json.dumps(dict(rows=rows,events=events,trace_overruns=overruns),indent=2));print(a.out,flush=True)
if __name__=='__main__':main()
