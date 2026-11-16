"""Bounded CPU depth preview session; systemd restores RGB capture on exit."""
import argparse,json,signal,subprocess,time
from pathlib import Path

# Conservative bench policy, not a measured thermal capability.
START_LIMIT_C=75.0
STOP_LIMIT_C=78.0

def thermal():
 temp=subprocess.check_output(['vcgencmd','measure_temp'],text=True,timeout=1).strip()
 flags=subprocess.check_output(['vcgencmd','get_throttled'],text=True,timeout=1).strip()
 return float(temp.split('=')[1].split("'")[0]),int(flags.split('=')[1],16)

def limit_reason(temp,flags,starting=False):
 import math
 if not math.isfinite(temp):return 'unknown_temperature'
 if flags&15:return 'active_limiting_flags'
 if temp >= (START_LIMIT_C if starting else STOP_LIMIT_C):return 'temperature_limit'
 return None

def main():
 p=argparse.ArgumentParser();p.add_argument('--preflight',action='store_true');p.add_argument('--seconds',type=int,default=60);p.add_argument('--map-interval-ms',type=int,default=0);p.add_argument('--map-on-demand',action='store_true');a=p.parse_args()
 if not 1<=a.seconds<=600:p.error('seconds must be 1..600')
 if not 0<=a.map_interval_ms<=900:p.error('map interval must be 0..900 ms')
 temp,flags=thermal();reason=limit_reason(temp,flags,True)
 if reason:raise SystemExit(reason)
 if a.preflight:return
 import sys
 stopped=False
 def stop(*_):
  nonlocal stopped
  stopped=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 child=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('preview.py')),'--host-stereo','--port','8096','--map-interval-ms',str(a.map_interval_ms)]+(['--map-on-demand'] if a.map_on_demand else []))
 deadline=time.monotonic()+a.seconds
 try:
  while not stopped and time.monotonic()<deadline and child.poll() is None:
   try:
    temp,flags=thermal();reason=limit_reason(temp,flags)
   except Exception as e:reason='thermal_read_failed:'+str(e)
   print(json.dumps(dict(kind='depth_session',temperature_c=temp,flags=flags,stop_reason=reason)),flush=True)
   if reason:break
   time.sleep(1)
 finally:
  if child.poll() is None:
   child.send_signal(signal.SIGINT)
   try:child.wait(timeout=10)
   except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':main()
