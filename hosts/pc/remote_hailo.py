"""Synchronous same-frame Hailo inference through an explicit localhost SSH tunnel."""
import hashlib,json,math,time,uuid
from urllib.request import Request,urlopen
from urllib.parse import urlsplit
import numpy as np

def validate_result(value,rid,digest,sha,instance=None):
 if value.get('request_id')!=rid or value.get('pixels_sha256')!=digest:raise ValueError('inference frame mismatch')
 if value.get('detector',{}).get('sha256')!=sha:raise ValueError('model changed')
 if instance is not None and value.get('detector',{}).get('instance')!=instance:raise ValueError('detector restarted; restart adapter to clear tracks')
 rows=value.get('detections')
 if not isinstance(rows,list) or len(rows)>1000:raise ValueError('invalid detections')
 for r in rows:
  if r.get('class_id')!=0 or r.get('label')!='person':raise ValueError('unexpected class')
  if not all(isinstance(r.get(k),(int,float)) and math.isfinite(r[k]) for k in ['x','y','w','h','confidence']):raise ValueError('nonfinite box')
  if not (0<=r['confidence']<=1 and r['x']>=0 and r['y']>=0 and r['w']>0 and r['h']>0 and r['x']+r['w']<=1280.01 and r['y']+r['h']<=720.01):raise ValueError('invalid box bounds')
 return rows

class RemoteHailo:
 def __init__(self,url):
  u=urlsplit(url)
  if u.scheme!='http' or u.hostname!='127.0.0.1' or u.path or u.query or u.fragment:raise ValueError('explicit loopback tunnel required')
  self.url=url
  with urlopen(url+'/health',timeout=2) as r:self.identity=json.load(r)
  if self.identity.get('engine')!='hailo10h-rpc':raise ValueError('wrong detector')
  self.engine='hailo10h-rpc';self.sha=self.identity['sha256'];self.name=self.identity['name'];self.last_remote={}
 def infer(self,rgb):
  if rgb.shape!=(720,1280,3) or rgb.dtype!=np.uint8:raise ValueError('expected 1280x720 RGB8')
  raw=np.ascontiguousarray(rgb[:,:,::-1]).tobytes();rid=uuid.uuid4().hex;digest=hashlib.sha256(raw).hexdigest()
  start=time.monotonic()
  req=Request(self.url+'/infer',data=raw,headers={'Content-Type':'application/x-bgr888','X-Frame-Request':rid})
  with urlopen(req,timeout=2) as r:
   if int(r.headers.get('Content-Length','0'))>1000000:raise ValueError('oversize result')
   value=json.loads(r.read(1000001))
  elapsed=time.monotonic()-start
  if elapsed>.5:raise ValueError('inference round trip too old')
  rows=validate_result(value,rid,digest,self.sha,self.identity['instance'])
  self.last_remote=dict(round_trip_ms=elapsed*1000,inference_ms=value['inference_ms'],instance=value['detector']['instance'])
  self.last_request=rid
  return rows

 def onboard(self, observation, frame):
  if self.identity.get('onboard')!='box-height-shadow-v1':raise ValueError('Pi does not support shadow commands')
  rid=self.last_request
  req=Request(self.url+'/onboard',data=json.dumps(dict(request_id=rid,observation=observation,frame=frame),allow_nan=False).encode(),headers={'Content-Type':'application/json'})
  with urlopen(req,timeout=.5) as r:
   raw=r.read(8193)
  if len(raw)>8192:raise ValueError('oversize shadow response')
  o=json.loads(raw)
  if o.get('request_id')!=rid or o.get('instance')!=self.identity['instance'] or o.get('frame_seq')!=frame['seq'] or o.get('frame_epoch')!=frame['epoch'] or o.get('selection_gen')!=frame['selection_gen']:
   raise ValueError('Pi shadow response identity mismatch')
  return o
