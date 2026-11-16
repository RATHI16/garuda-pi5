#!/usr/bin/env python3
"""Short functional host-stereo baseline; polls actual producer counters, keeps errors."""
import argparse,json,time,threading,urllib.request,subprocess

def get(url):
 with urllib.request.urlopen(url,timeout=3) as r:return json.load(r)

def main():
 p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=30);p.add_argument('--out',required=True);a=p.parse_args()
 stop=threading.Event()
 def view():
  try:
   with urllib.request.urlopen('http://localhost:8098/stream',timeout=5) as r:
    while not stop.is_set():r.read(8192)
  except Exception:pass
 threading.Thread(target=view,daemon=True).start();records=[]
 for _ in range(a.seconds):
  start=time.monotonic();row={'monotonic':start}
  for name,url in [('camera','http://localhost:8096/health'),('mono','http://localhost:8098/api/state')]:
   try:
    d=get(url)
    row[name]=d if name=='camera' else {k:d.get(k) for k in ['frame','stats','detector']}
   except Exception as e:row[name]={'error':str(e)}
  row['thermal']=subprocess.check_output(['vcgencmd','measure_temp'],text=True).strip()
  row['throttle']=subprocess.check_output(['vcgencmd','get_throttled'],text=True).strip()
  row['cpu']=open('/proc/stat').read().split('intr')[0]
  records.append(row);time.sleep(max(0,1-(time.monotonic()-start)))
 stop.set();open(a.out,'w').write(json.dumps(records,indent=2))
 print('Recorded',len(records),'samples to',a.out)
if __name__=='__main__':main()
