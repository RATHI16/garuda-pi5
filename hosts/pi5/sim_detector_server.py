"""Loopback-only, simulation-gated YOLO26 RPC. AGPL evaluation, not-product-cleared."""
import hashlib,json,os,signal,time,uuid
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import numpy as np

SIZE=1280*720*3

def validate_request(length,request_id):
 if length!=SIZE or not request_id or len(request_id)>64:raise ValueError('invalid frame envelope')

def make_handler(detector,identity,cache,stop,shadow,limit_reason):
 class HTTP(BaseHTTPRequestHandler):
  def setup(self):super().setup();self.connection.settimeout(3)
  def log_message(self,*args):pass
  def reply(self,code,data):
   body=json.dumps(data,allow_nan=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  def do_GET(self):
   self.reply(200,dict(identity,thermal=cache.snapshot())) if self.path=='/health' else self.reply(404,{})
  def do_POST(self):
   if self.path=='/onboard':
    try:
     n=int(self.headers.get('Content-Length','0'))
     if self.headers.get('Content-Type')!='application/json' or not 0<n<=8192:raise ValueError('invalid command envelope')
     temp,flags=cache.get()
     if limit_reason(temp,flags):self.reply(503,{'error':'thermal_limit'});stop();return
     self.reply(200,shadow.compute(json.loads(self.rfile.read(n))))
    except (ValueError,TypeError,KeyError) as e:self.reply(400,{'error':str(e)})
    except Exception as e:self.reply(503,{'error':str(e)})
    return
   if self.path!='/infer':self.reply(404,{});return
   try:
    rid=self.headers.get('X-Frame-Request');n=int(self.headers.get('Content-Length','0'));validate_request(n,rid)
    if self.headers.get('Content-Type')!='application/x-bgr888':raise ValueError('BGR888 required')
    raw=self.rfile.read(n)
    if len(raw)!=SIZE:raise ValueError('truncated frame')
    temp,flags=cache.get()
    if limit_reason(temp,flags):self.reply(503,{'error':'thermal_limit'});stop();return
    image=np.frombuffer(raw,np.uint8).reshape(720,1280,3)
    start=time.monotonic_ns();dets=detector.infer(image);elapsed=(time.monotonic_ns()-start)/1e6
    rows=[dict(x=float(d.x),y=float(d.y),w=float(d.w),h=float(d.h),confidence=float(d.conf),class_id=0,label='person') for d in dets if d.class_id==0]
    shadow.remember(rid,rows)
    self.reply(200,dict(request_id=rid,pixels_sha256=hashlib.sha256(raw).hexdigest(),detector=identity,inference_ms=elapsed,detections=rows))
   except (ValueError,TimeoutError) as e:self.reply(400,{'error':str(e)})
   except Exception as e:self.reply(503,{'error':str(e)})
 return HTTP


def main():
 if os.environ.get('GARUDA_SIM')!='1':raise SystemExit('GARUDA_SIM=1 required')
 from sim_onboard import SimOnboard
 import hailo_yolo26
 from hailo_yolo26 import HailoYolo26
 from oak.depth_session import thermal,limit_reason
 from thermal_cache import ThermalCache
 cache=ThermalCache(thermal)
 temp,flags=cache.get()
 if limit_reason(temp,flags,True):raise SystemExit('Pi not cool enough')
 hef=Path('/home/garuda/garuda-sdk-deploy/models/yolo26m.hef')
 identity=dict(wrapper_sha256=hashlib.sha256(Path(hailo_yolo26.__file__).read_bytes()).hexdigest(),engine='hailo10h-rpc',name='YOLO26m · Pi Hailo-10H',sha256=hashlib.sha256(hef.read_bytes()).hexdigest(),instance=uuid.uuid4().hex,source='gazebo-rgb',conf_threshold=.15)
 shadow=SimOnboard(identity['instance'])
 identity['onboard']='box-height-shadow-v1'
 import onboard_command
 identity['onboard_sha256']=hashlib.sha256(Path(onboard_command.__file__).read_bytes()).hexdigest()
 identity['server_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 detector=HailoYolo26(hef,conf_thresh=.15);stopping=False
 def stop(*_):
  nonlocal stopping
  stopping=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 HTTP=make_handler(detector,identity,cache,stop,shadow,limit_reason)
 server=HTTPServer(('127.0.0.1',8102),HTTP);server.timeout=1
 try:
  while not stopping:
   temp,flags=cache.get()
   if limit_reason(temp,flags):break
   server.handle_request()
 finally:server.server_close();detector.close()

if __name__=='__main__':main()
