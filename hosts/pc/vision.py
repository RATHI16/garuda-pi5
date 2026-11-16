"""Local PC evaluation detector and replay. Never connects to a flight controller.

Gazebo RGBD capture reused from the Hailo-15 bench; YOLOX inference is PC CPU,
not a Hailo benchmark. No actor coordinates or annotation boxes enter detection.
"""
import argparse
from collections import OrderedDict, deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import sys
import subprocess
import threading
import time
import uuid
from urllib.parse import urlparse, parse_qs

import cv2
import numpy as np
from gazebo_pixels import GazeboPixels, depth_range, jpeg

PC_BOOT_ID=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
MAX_CONTINUITY_GAP_S=.5  # bench policy; wall time, not number of updates
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'services/garuda-follow'))
from tracker import Tracker
from onboard_source import PROVENANCE

class Detector:
    def __init__(self, model, engine='ort'):
        cv2.setNumThreads(4)
        self.engine=engine
        if engine=='ort':
            import onnxruntime as ort
            options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
            options.add_session_config_entry('session.intra_op.allow_spinning','0')
            self.net=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
            self.input_name=self.net.get_inputs()[0].name
        else:self.net=cv2.dnn.readNetFromONNX(str(model))
        self.sha=hashlib.sha256(Path(model).read_bytes()).hexdigest()
        self.grid=np.concatenate([np.stack(np.meshgrid(np.arange(640//s),np.arange(640//s)),axis=-1).reshape(-1,2) for s in (8,16,32)])
        self.stride=np.concatenate([np.full((640//s)**2,s) for s in (8,16,32)])[:,None]

    def infer(self,rgb):
        h,w=rgb.shape[:2];ratio=min(640/w,640/h)
        padded=np.full((640,640,3),114,dtype=np.uint8)
        padded[:int(h*ratio),:int(w*ratio)]=cv2.resize(rgb,(int(w*ratio),int(h*ratio)))
        tensor=padded.transpose(2,0,1)[None].astype(np.float32)
        if self.engine=='ort':raw=self.net.run(None,{self.input_name:tensor})[0][0]
        else:
            self.net.setInput(tensor);raw=self.net.forward()[0]
        # Decode anchor-free centre/size predictions. Person class is COCO index 0.
        scores=raw[:,4]*raw[:,5];indices=np.flatnonzero(scores>=.35)
        if not len(indices):return []
        centres=(raw[indices,:2]+self.grid[indices])*self.stride[indices]
        sizes=np.exp(raw[indices,2:4])*self.stride[indices]
        boxes=np.concatenate((centres-sizes/2,sizes),axis=1)/ratio
        keep=cv2.dnn.NMSBoxes(boxes.tolist(),scores[indices].tolist(),.35,.45)
        results=[]
        for k in np.asarray(keep).reshape(-1):
            x,y,bw,bh=map(float,boxes[k]);x0=max(0.,x);y0=max(0.,y)
            bw=min(float(w),x+bw)-x0;bh=min(float(h),y+bh)-y0
            if bw>3 and bh>5:results.append(dict(x=x0,y=y0,w=bw,h=bh,confidence=float(scores[indices[k]]),class_id=0,label='person'))
        return results

def catalog(root):
    """Existing Hailo-15 annotated aerial data, referenced in place."""
    base=Path(root)/'datasets/visdrone/VisDrone2019-SOT-val/sequences'
    result={}
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if folder.is_dir() and any(folder.glob('*.jpg')):
                result[folder.name]=dict(path=str(folder),label='VisDrone · '+folder.name,kind='images',fps=30)
    return result

class Replay:
    def __init__(self,entry):
        self.entry=entry;self.files=sorted(Path(entry['path']).glob('*.jpg')) if entry['kind']=='images' else []
        self.cap=None if self.files else cv2.VideoCapture(entry['path'])
        if self.cap and not self.cap.isOpened():raise ValueError('Cannot open video')
        self.total=len(self.files) if self.files else int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps=entry.get('fps',30) if self.files else self.cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(self.fps) or self.fps<=0:self.fps=30
        self.index=0;self.started=time.monotonic();self.done=False
    def read(self):
        if self.index>=self.total:self.done=True;return None
        if self.files:bgr=cv2.imread(str(self.files[self.index]))
        else:ok,bgr=self.cap.read();bgr=bgr if ok else None
        if bgr is None:self.done=True;return None
        self.index+=1
        return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
    def close(self):
        if self.cap:self.cap.release()

class Vision:
    def __init__(self,detector,clips,data_root=None,camera_pitch=.12):
        if not np.isfinite(camera_pitch) or not 0<=camera_pitch<=.6:raise ValueError("invalid camera pitch")
        self.camera_pitch=float(camera_pitch)
        self.detector=detector;self.clips=clips;self.pixels=GazeboPixels()
        self.data_root=str(data_root or ROOT/'datasets')
        self.lock=threading.RLock();self.frames=OrderedDict();self.state={};self.error='Waiting for camera'
        self.onboard_enabled=False;self.onboard_target=None;self.onboard_generation=0
        self.mode='gazebo';self.epoch=uuid.uuid4().hex;self.version=0;self.replay=None
        self.paused=True;self.rate=1.;self.running=True;self.tracker=Tracker(max_age=3,max_unmatched_s=.5)
        self.timings=deque(maxlen=300);self.processed=0;self.started=time.monotonic()
        self.last_gz=0;self.last_at=0.;self.last_seq=0
        self.benchmark=None;self.report=None
        self.last_processed_at=None;self.continuity_valid=False
        self.changed=threading.Condition(self.lock);self.stream_id=uuid.uuid4().hex;self.revision=0
    def advance_revision(self):
        with self.changed:
            self.revision+=1;self.changed.notify_all()

    def cursor(self):return f'{self.stream_id}:{self.revision}'

    def next_snapshot(self,after=None,wait_s=.25):
        """Latest-state long poll, bounded wait; invalidation is also a publication."""
        deadline=time.monotonic()+wait_s
        with self.changed:
            self.check_continuity(time.monotonic())
            while after==self.cursor():
                left=deadline-time.monotonic()
                if left<=0:return None
                self.changed.wait(min(left,.05))
                self.check_continuity(time.monotonic())
            value=self.snapshot();value['stream_cursor']=self.cursor()
            return value

    def invalidate(self,reason,force=False):
        """Caller holds lock. Change epoch once per outage; discard in-flight results."""
        if self.continuity_valid or force:
            self.version+=1;self.epoch=uuid.uuid4().hex;self.tracker=Tracker(max_age=3,max_unmatched_s=.5)
            self.state={};self.frames.clear();self.continuity_valid=False;self.onboard_target=None
            self.last_processed_at=None
            self.advance_revision()
        self.error=reason

    def check_continuity(self,now):
        if self.last_processed_at is not None and now-self.last_processed_at>MAX_CONTINUITY_GAP_S:
            self.invalidate('Perception gap: select target again')

    def configure(self,data):
        with self.lock:
            if 'onboard_select' in data:
                tid=data['onboard_select'];frame=self.state.get('frame',{})
                generation=data.get('selection_gen')
                if type(generation) is not int or generation<0:raise ValueError('Selection generation required')
                if not self.onboard_enabled:raise ValueError('Onboard source changed')
                if tid is not None:
                    if self.paused or data.get('epoch')!=frame.get('epoch') or data.get('instance')!=self.state.get('stats',{}).get('instance'):
                        raise ValueError('Onboard source or frame changed')
                    if time.monotonic()-self.last_at>.4:raise ValueError('Selection frame stale')
                    if type(tid) is not int or not any(t.get('id')==tid and not t.get('coasting') and t.get('label')=='person' for t in self.state.get('tracks',[])):
                        raise ValueError('Selected person unavailable')
                self.onboard_target=tid;self.onboard_generation=generation
                self.version+=1;self.state.pop('onboard',None);self.advance_revision()
                return
            if 'benchmark' in data:
                clip=data['benchmark']
                if clip not in self.clips:raise ValueError('Unknown replay clip')
                if self.benchmark and self.benchmark.poll() is None:raise ValueError('Benchmark already running')
                self.report=ROOT/'.runtime/reports'/('replay-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.json')
                self.report.parent.mkdir(parents=True,exist_ok=True)
                with self.report.with_suffix('.log').open('wb') as log:
                    cmd=[sys.executable,str(ROOT/'bench/vision_replay.py'),'--clip',clip,'--out',str(self.report),'--data-root',self.data_root]
                    if self.clips[clip]['kind']=='video':cmd.extend(['--video',self.clips[clip]['path']])
                    self.benchmark=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
                return
            if 'paused' in data:
                self.paused=bool(data['paused'])
                if self.paused:self.invalidate('Perception paused: select target again')
            if 'rate' in data:
                rate=float(data['rate'])
                if not math.isfinite(rate) or not .25<=rate<=2:raise ValueError('Replay rate must be 0.25–2')
                self.rate=rate
            if 'mode' in data:
                mode=data['mode'];onboard=mode=='onboard'
                if onboard:
                    if self.detector.engine!='hailo10h-rpc' or not callable(getattr(self.detector,'onboard',None)):
                        raise ValueError('Onboard rehearsal needs Pi Hailo RPC with shadow support')
                    mode='gazebo'
                if mode not in ('gazebo','replay'):raise ValueError('Invalid vision mode')
                new=Replay(self.clips[data['clip']]) if mode=='replay' else None
                if self.replay:self.replay.close()
                self.mode=mode;self.onboard_enabled=onboard;self.replay=new;self.paused=False;self.version+=1
                self.invalidate('Source changed: select target again',force=True)
                self.timings.clear();self.processed=0;self.started=time.monotonic();self.last_gz=0
    def snapshot(self):
        with self.lock:
            self.check_continuity(time.monotonic())
            out=dict(self.state);out['age_ms']=(time.monotonic()-self.last_at)*1000 if self.state else None
            frame,depth,clock,rtf=self.pixels.snapshot()
            if self.mode=='gazebo' and (not clock or time.monotonic_ns()-clock[1]>500_000_000):out['age_ms']=1e9
            out.update(error=self.error,kind=self.mode,paused=self.paused,rate=self.rate,rtf=rtf,
                benchmark='running' if self.benchmark and self.benchmark.poll() is None else 'complete' if self.report and self.report.exists() else 'failed' if self.benchmark else 'idle',
                replay=dict(frame=self.replay.index,total=self.replay.total,done=self.replay.done) if self.replay else None)
            return out
    def run(self):
        next_due=0
        while self.running:
            try:
                with self.lock:
                    self.check_continuity(time.monotonic())
                    version=self.version;mode=self.mode;epoch=self.epoch
                    if self.paused:frame=None
                    elif mode=='gazebo':
                        item,depth,clock,rtf=self.pixels.snapshot()
                        frame=item if item and item[0]!=self.last_gz else None
                        if frame:self.last_gz=frame[0]
                    elif self.replay and time.monotonic()>=next_due:
                        rgb=self.replay.read();depth=None
                        frame=(self.replay.index,0,time.monotonic_ns(),rgb) if rgb is not None else None
                        next_due=time.monotonic()+1/(self.replay.fps*self.rate)
                    else:frame=None
                if frame is None:time.sleep(.01);continue
                seq,sim_ns,ingress,rgb=frame;begin=time.monotonic();dets=self.detector.infer(rgb)
                finished=time.monotonic();infer_ms=(finished-begin)*1000
                with self.lock:
                    self.check_continuity(finished)
                    if version!=self.version:continue
                    tracks=self.tracker.update(dets)
                    observations=[]
                    if mode=='gazebo' and depth is None:
                        with self.pixels.lock:depth=self.pixels.depths.get(sim_ns)
                    for t in tracks:
                        box=dict(zip(('x','y','w','h'),t.bbox))
                        observations.append(dict(id=t.id,**box,conf=t.confidence,coasting=False,label='person',
                            range_m=depth_range(depth,box) if mode=='gazebo' else None,gesture=dict(name=None)))
                    fseq=self.last_seq+1
                    enabled,selected,generation=self.onboard_enabled,self.onboard_target,self.onboard_generation
                command=None
                if enabled:
                    target=next((t for t in observations if t['id']==selected),None)
                    obs=dict(frame_w=rgb.shape[1],frame_h=rgb.shape[0],age_ms=(time.monotonic_ns()-ingress)/1e6,
                             identity=None,range_source='box_height',stereo=None,target=target,selected_id=selected)
                    # Network wait must not block state reads, selection, pause or source change.
                    command=self.detector.onboard(obs,dict(seq=fseq,epoch=epoch,selection_gen=generation))
                    for track in observations:track['range_m']=None
                finished=time.monotonic()
                with self.lock:
                    self.check_continuity(finished)
                    if version!=self.version or (enabled,selected,generation)!=(self.onboard_enabled,self.onboard_target,self.onboard_generation):continue
                    self.last_processed_at=finished;self.continuity_valid=True
                    self.last_at=ingress/1e9;self.last_seq=fseq;self.processed+=1;self.timings.append(infer_ms)
                    elapsed=max(.001,finished-self.started)
                    self.state=dict(frame=dict(seq=fseq,epoch=epoch,width=rgb.shape[1],height=rgb.shape[0],sim_ns=sim_ns,ingress_monotonic_ns=ingress,clock='pc-monotonic',clock_boot_id=PC_BOOT_ID),
                        tracks=observations,detector=dict(mode='person',name=getattr(self.detector,'name','YOLOX-S · PC CPU'),engine=self.detector.engine,sha256=self.detector.sha),
                        camera=dict(hfov=1.2,pitch=self.camera_pitch,depth_paired=depth is not None),
                        metrics=dict(frames=self.processed,processing_fps=self.processed/elapsed,inference_ms=infer_ms,
                            inference_p95_ms=float(np.percentile(self.timings,95)),ingress_to_result_ms=(finished-ingress/1e9)*1000),kind=mode)
                    self.state['metrics']['remote']=getattr(self.detector,'last_remote',None)
                    if enabled:
                        self.state.update(onboard=command,onboard_provenance=PROVENANCE,
                            stats=dict(instance=command['instance'],capabilities=dict(camera='gazebo',mount='vehicle',distance_source='none')))
                        self.state['metrics']['ingress_to_shadow_ms']=(time.monotonic_ns()-ingress)/1e6
                    self.frames[(epoch,fseq)]=jpeg(rgb)
                    while len(self.frames)>8:self.frames.popitem(last=False)
                    self.error='';self.advance_revision()
            except Exception as exc:
                with self.lock:self.invalidate('Inference error: '+str(exc),force=True)
                time.sleep(.2)

def http_handler(vision):
    class HTTP(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,data,kind='application/json'):
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            parsed=urlparse(self.path)
            if parsed.path=='/api/next':
                q=parse_qs(parsed.query,keep_blank_values=True)
                if set(q)-{'after'} or any(len(v)!=1 for v in q.values()) or ('after' in q and not 1<=len(q['after'][0])<=128):
                    self.reply(400,b'{}');return
                value=vision.next_snapshot(q.get('after',[None])[0])
                self.reply(204,b'') if value is None else self.reply(200,json.dumps(value,allow_nan=False).encode())
            elif parsed.path=='/api/state':self.reply(200,json.dumps(vision.snapshot(),allow_nan=False).encode())
            elif parsed.path=='/api/clips':self.reply(200,json.dumps([dict(id=k,label=v['label']) for k,v in vision.clips.items()]).encode())
            elif parsed.path=='/api/report':
                with vision.lock:path=vision.report
                self.reply(200,path.read_bytes()) if path and path.exists() else self.reply(404,b'{}')
            elif parsed.path=='/frame.jpg':
                q=parse_qs(parsed.query)
                try:key=(q['epoch'][0],int(q['seq'][0]))
                except (KeyError,ValueError):self.reply(400,b'{}');return
                with vision.lock:frame=vision.frames.get(key)
                self.reply(200,frame,'image/jpeg') if frame else self.reply(410,b'{}')
            elif parsed.path=='/overview.jpg':
                with vision.pixels.lock:frame=vision.pixels.overview
                if frame and time.monotonic_ns()-frame[0]<2e9:self.reply(200,frame[1],'image/jpeg')
                else:self.reply(503,b'{}')
            else:self.reply(404,b'{}')
        def do_POST(self):
            if self.path!='/api/config' or self.headers.get('Content-Type')!='application/json':self.reply(400,b'{}');return
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{self.server.server_port}') :self.reply(403,b'{}');return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<2048:raise ValueError('body size')
                vision.configure(json.loads(self.rfile.read(size)));self.reply(200,b'{"ok":true}')
            except (KeyError,ValueError,TypeError) as exc:self.reply(400,json.dumps(dict(error=str(exc))).encode())
    return HTTP


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8101)
    p.add_argument('--camera-pitch',type=float,default=.12,help='Gazebo camera mount pitch, must match world')
    p.add_argument('--hailo-rpc',help='Explicit loopback SSH tunnel to Pi simulation detector')
    p.add_argument('--model',type=Path,default=ROOT/'.runtime/models/yolox/object_detection_yolox_2022nov.onnx')
    p.add_argument('--data-root',default=str(ROOT/'datasets'))
    p.add_argument('--video',type=Path,action='append',default=[]);a=p.parse_args()
    if os.environ.get('GARUDA_SIM')!='1':raise SystemExit('GARUDA_SIM=1 required')
    clips=catalog(a.data_root)
    for path in a.video:
        path=path.resolve()
        if not path.is_file():raise SystemExit(f'Video missing: {path}')
        clips[path.stem]=dict(path=str(path),label=path.name,kind='video')
    if a.hailo_rpc:
        from remote_hailo import RemoteHailo
        detector=RemoteHailo(a.hailo_rpc)
    else:detector=Detector(a.model)
    vision=Vision(detector,clips,a.data_root,a.camera_pitch)
    threading.Thread(target=vision.run,daemon=True).start()
    HTTP=http_handler(vision)
    try:ThreadingHTTPServer(('127.0.0.1',a.port),HTTP).serve_forever()
    finally:vision.running=False

if __name__=='__main__':main()
