"""One bounded new-frame request; no ROS dependency or cross-host clock arithmetic."""
import json,math,time
from urllib.parse import urlencode
from urllib.request import urlopen
from pose_history import PC_BOOT_ID

def fixed_stereo_decode(snapshot):
 import sys
 from pathlib import Path
 pc=str(Path(__file__).resolve().parents[2]/'hosts/pc')
 if pc not in sys.path:sys.path.insert(0,pc)
 from fixed_stereo_observation import decode
 return decode(snapshot)

class ObservationClient:
 def __init__(self):self.key=None;self.cursor=None
 def read(self,selected):
  key=(selected['generation'],selected['kind'],selected['url'])
  if key!=self.key:self.key=key;self.cursor=None
  local=selected['kind'] in ('gazebo','replay','onboard');stereo=selected['kind']=='fixed_stereo';waiting=selected['kind'] in ('fixed_stereo','mono_estimate','pi_onboard')
  path='/api/next' if local else '/api/state'
  # Fixed stereo: the Pi detector holds the answer until it has a newer result (no polling delay).
  if waiting:path+='?'+urlencode({'after':-1 if self.cursor is None else self.cursor})
  if local and self.cursor:path+='?'+urlencode({'after':self.cursor})
  start=time.monotonic()
  with urlopen(selected['url']+path,timeout=1.5 if waiting else 1) as r:
   if r.status==204:return None
   data=json.load(r)
  if local:
   cursor=data.get('stream_cursor')
   if not isinstance(cursor,str) or not 1<=len(cursor)<=128:raise ValueError('missing observation cursor')
   if cursor==self.cursor:raise ValueError('duplicate observation cursor')
   frame=data.get('frame',{})
   if frame:
    stamp=frame.get('ingress_monotonic_ns')
    if frame.get('clock')!='pc-monotonic' or frame.get('clock_boot_id')!=PC_BOOT_ID or type(stamp) is not int:
     raise ValueError('incompatible observation clock')
    age=(time.monotonic_ns()-stamp)/1e6
    if age<0:raise ValueError('future observation')
    # Preserve producer invalidity (e.g. stopped simulator clock); never make
    # a deliberately stale observation fresh by recomputing ingress age.
    reported=data.get('age_ms')
    data['age_ms']=max(age,reported) if isinstance(reported,(int,float)) and math.isfinite(reported) else None
   self.cursor=cursor
  else:
   elapsed=(time.monotonic()-start)*1000
   if waiting:
    # Decoded from the snapshot exactly as received. The Pi's capture clock is never mixed
    # with this machine's clock: only the producer's age plus time spent here is used.
    if stereo:data['fixed_stereo']=fixed_stereo_decode(data)
    seq=data.get('frame',{}).get('seq')
    if type(seq) is int:self.cursor=seq
    # Time the detector deliberately held the request is already inside its reported age;
    # an unreported or implausible hold counts as none, which only makes the age older.
    waited=data.pop('waited_ms',None)
    if isinstance(waited,(int,float)) and not isinstance(waited,bool) and math.isfinite(waited) and 0<=waited<=elapsed:elapsed-=waited
   data['tracks']=[t for t in data.get('tracks',[]) if t.get('label')=='person']
   data['metrics']={'processing_fps':data.get('stats',{}).get('fps')}
   age=data.get('age_ms')
   if isinstance(age,(float,int)):data['age_ms']=age+elapsed
  data['kind']=selected['kind']
  return data
