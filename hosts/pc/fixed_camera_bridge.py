#!/usr/bin/env python3
"""Local read-only camera/Gazebo diagnostic. No flight command interfaces."""
import argparse, json, os, subprocess, threading, time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, parse_qs
from fixed_camera import FixedCamera, finite

class Bridge:
    def __init__(self, camera, simulator, calibration, selection='click', detector_read='poll'):
        if selection not in ('click','id'):raise ValueError('selection must be click or id')
        if detector_read not in ('poll','wait'):raise ValueError('detector_read must be poll or wait')
        self.camera, self.simulator, self.selection = camera, simulator, selection
        self.wait=detector_read=='wait';self.last_seq=None
        self.core = FixedCamera(calibration)
        self.lock = threading.Lock()
        self.source = {}; self.sim = {}; self.at = 0
        self.result = {'status':'unavailable','reason':'Connecting','position_m':None}
        self.stop = threading.Event()
        self.marker_error = ''
        self.depth_reader=None;self.box_reader=None;self.history=deque(maxlen=80)
        self.marker_shown=False;self.marker_at=0
        self.polls=deque(maxlen=40);self.seen=None;self.counts=dict(polls=0,poll_errors=0,distinct_results=0,inferences_first=None,inferences_last=None,wait_unreported=0,wait_no_newer_result=0)
    def marker_due(self, point, now):
        """Gazebo is contacted only to show, refresh or remove a marker. With nothing shown
        and nothing to show there is no request, so detector polling is not delayed."""
        if point is None:return self.marker_shown
        return not self.marker_shown or now-self.marker_at>=.25
    def note_poll(self, s, now):
        """Latest-state polling can skip detector results; count what was actually seen."""
        f=s.get('frame',{});key=(s.get('stats',{}).get('instance'),f.get('epoch'),f.get('seq'))
        n=s.get('stats',{}).get('inferences');c=self.counts
        c['polls']+=1;self.polls.append(now)
        if key[0] is None or key[2] is None:return
        if self.seen is None or key[:2]!=self.seen[:2]:
            c.update(distinct_results=0,inferences_first=n,inferences_last=n);self.seen=None
        if key!=self.seen:c['distinct_results']+=1;self.seen=key
        c['inferences_last']=n
    def delivery(self):
        """Caller holds the lock. Produced counts results since this process/epoch was first seen."""
        c=dict(self.counts);a,b=c.pop('inferences_first'),c.pop('inferences_last')
        c['detector_results_produced']=b-a+1 if type(a) is int and type(b) is int and b>=a else None
        c['poll_hz']=(len(self.polls)-1)/(self.polls[-1]-self.polls[0]) if len(self.polls)>1 and self.polls[-1]>self.polls[0] else None
        return c
    def marker(self, point=None):
        req = 'ns: "garuda_fixed_camera" id: 1 action: DELETE_MARKER'
        if point is not None:
            x,y,z = point
            req = ('ns: "garuda_fixed_camera" id: 1 action: ADD_MODIFY type: SPHERE '
                   f'pose: {{position: {{x: {x} y: {y} z: {z}}}}} '
                   'scale: {x: 0.25 y: 0.25 z: 0.25} lifetime: {sec: 1} '
                   'material: {diffuse: {r: 0 g: 1 b: 0 a: 1}} visibility: GUI')
        try:
            from google.protobuf import text_format
            from gz.transport13 import Node
            from gz.msgs10.marker_pb2 import Marker
            from gz.msgs10.empty_pb2 import Empty
            from gz.msgs10.marker_v_pb2 import Marker_V
            if not hasattr(self, 'node'):
                os.environ['GZ_PARTITION']='garuda_isolated'
                self.node=Node()
            msg=Marker();text_format.Parse(req,msg)
            # /marker is one-way: its Empty response does not acknowledge success.
            self.node.request('/marker',msg,Marker,Empty,10)
            ok,listed=self.node.request('/marker/list',Empty(),Empty,Marker_V,100)
            present=any(m.ns=='garuda_fixed_camera' and m.id==1 for m in listed.marker)
            self.marker_error='' if ok and present==(point is not None) else 'Gazebo marker update not yet confirmed'
            # A removal is retried until Gazebo confirms the marker is gone.
            self.marker_shown=point is not None or not (ok and not present);self.marker_at=time.monotonic()
        except Exception: self.marker_error='Gazebo marker service unavailable'
    def run(self):
        try:
            while not self.stop.is_set():
                start = time.monotonic()
                repeat=False
                try:
                    # wait mode: the detector holds the answer until it has a newer result.
                    query=f'?after={-1 if self.last_seq is None else self.last_seq}' if self.wait else ''
                    with urlopen(self.camera+'/api/state'+query,timeout=1.5 if self.wait else .4) as r: s=json.load(r)
                    if not finite(s.get('age_ms')):raise LookupError('detector has no processed frame yet')
                    got=time.monotonic();s['age_ms'] += self.transit_ms(s,(got-start)*1000)
                    seq=s.get('frame',{}).get('seq');repeat=self.wait and seq==self.last_seq;self.last_seq=seq
                    # Asked for before anything else: the stereo request does not wait for the simulator read.
                    if self.box_reader:self.box_reader.submit(self.box_job(s,got))
                    with urlopen(self.simulator+'/api/state',timeout=.4) as r: sim=json.load(r)
                    with self.lock:
                        self.source,self.sim,self.at=s,sim,got
                        self.history.append((self.at,s));self.note_poll(s,self.at)
                        self.result=self.core.evaluate(s,sim)
                    # A detector result can arrive after its depth pair: retry the cached pair.
                    if self.depth_reader:self.depth_reader.associate()
                except Exception as e:
                    repeat=True
                    with self.lock:
                        self.core.invalidate(); self.source={};self.counts['poll_errors']+=1
                        self.result={'status':'unavailable','reason':'Source unavailable: '+(str(e) if isinstance(e,LookupError) else type(e).__name__),'position_m':None}
                point=self.result.get('position_m')
                if self.marker_due(point,time.monotonic()):self.marker(point)
                if not self.wait or repeat:
                    # Polling pace. In wait mode this applies only when no newer result came back
                    # (error, the detector's own wait limit, or a detector without the cursor).
                    if self.wait and time.monotonic()-start<.5:
                        with self.lock:self.counts['wait_no_newer_result']+=1
                    self.stop.wait(max(0,.05-(time.monotonic()-start)))
        finally:
            if self.marker_shown:self.marker()
    def transit_ms(self, s, elapsed_ms):
        """Time to add to the detector's reported age. The age was computed when the answer
        was built, so only the part of the request not spent held by the detector counts.
        An unreported or implausible hold counts as zero, which keeps the age conservative."""
        if not self.wait:return elapsed_ms
        waited=s.pop('waited_ms',None)
        if not finite(waited) or not 0<=waited<=elapsed_ms:
            with self.lock:self.counts['wait_unreported']+=1
            return elapsed_ms
        return elapsed_ms-waited
    def snapshot(self):
        with self.lock:
            out=dict(self.result)
            if time.monotonic()-self.at>.7:
                out.update(status='unavailable',reason='Camera connection stale',position_m=None)
            out.update(camera=self.camera,person=self.source.get('lock'),frame=self.source.get('frame'),
                       marker_error=self.marker_error,flight_enabled=False,selection_mode=self.selection,detector_read='wait' if self.wait else 'poll',delivery=self.delivery(),detector_fps=self.source.get('stats',{}).get('fps'),
                       people=[{k:t.get(k) for k in ('id','label','conf','x','y','w','h')} for t in self.source.get('tracks',[]) if not t.get('coasting') and time.monotonic()-self.at<.7])
            return out
    def selection_key(self):
        with self.lock:
            s=self.source;f=s.get('frame',{});tid=s.get('lock',{}).get('id')
            if time.monotonic()-self.at>.7 or not finite(s.get('age_ms')) or s['age_ms']+(time.monotonic()-self.at)*1000>=700:return None
            t=next((t for t in s.get('tracks',[]) if t.get('id')==tid and t.get('label')=='person' and not t.get('coasting')),None)
            return [s.get('stats',{}).get('instance'),f.get('epoch'),tid] if t and s.get('stats',{}).get('instance') else None
    def box_job(self, s, at):
        """Request for stereo in the selected person's torso region of one processed
        frame, or None when no visible person is selected in it."""
        from stereo_person_preview import torso_region
        f=s.get('frame',{});tid=s.get('lock',{}).get('id');instance=s.get('stats',{}).get('instance')
        t=next((t for t in s.get('tracks',[]) if t.get('id')==tid and t.get('label')=='person' and not t.get('coasting')),None)
        epoch=f.get('epoch');capture=f.get('capture_host_ns')
        if not t or not instance or not isinstance(epoch,str) or not epoch.startswith('oak-rgb:') or type(capture) is not int:return None
        if (f.get('width'),f.get('height'))!=(1280,720) or not all(finite(t.get(k)) for k in ('x','y','w','h')):return None
        region=torso_region([t[k] for k in ('x','y','w','h')],1280,720)
        if region is None:return None
        return dict(key=[instance,epoch,tid],frame=(instance,epoch,f.get('seq')),owner_epoch=epoch[8:].split(':')[0],
                    capture=capture,region=region,at=at,age_ms=s['age_ms'])
    def person_depth(self, meta, depth):
        from stereo_person_preview import torso_stat
        key=self.selection_key()
        if key is None:return {'reason':'Select a visible person'}
        epoch=meta.get('epoch');stamp=meta.get('rgb',{}).get('capture_host_ns')
        if not isinstance(stamp,int):return {'reason':'Stereo frame timestamp missing'}
        with self.lock:history=list(self.history)
        for at,s in reversed(history):
            f=s.get('frame',{});capture=f.get('capture_host_ns')
            if [s.get('stats',{}).get('instance'),f.get('epoch'),key[2]]!=key:continue
            if not str(f.get('epoch','')).startswith('oak-rgb:'+str(epoch)+':'):continue
            if not isinstance(capture,int) or abs(capture-stamp)>1000:continue
            age=s.get('age_ms',10000)+(time.monotonic()-at)*1000
            if age>=700:return {'reason':'Matching detection is stale'}
            t=next((t for t in s.get('tracks',[]) if t.get('id')==key[2] and t.get('label')=='person' and not t.get('coasting')),None)
            if not t:return {'reason':'Person absent in matching frame'}
            if f.get('width')!=depth.shape[1] or f.get('height')!=depth.shape[0]:return {'reason':'Depth and detection dimensions differ'}
            result=torso_stat(depth,[t[k] for k in ('x','y','w','h')])
            result.update(selection_key=key,age_ms=age,rgb_depth_skew_ms=meta.get('pair_skew_ns',0)/1e6)
            return result
        return {'reason':'Waiting for stereo paired with a processed detection frame'}
    def choose(self, tid, epoch):
        with self.lock:
            age=self.source.get('age_ms',10000)+(time.monotonic()-self.at)*1000
            if not finite(age) or age>=700 or self.source.get('frame',{}).get('epoch')!=epoch:
                raise ValueError('Image changed; select again')
            if not (self.sim.get('armed') is False and self.sim.get('landed') is True and self.sim.get('telemetry_fresh') is True):
                raise ValueError('Simulator must be grounded')
            t=next((t for t in self.source.get('tracks',[]) if t.get('id')==tid and not t.get('coasting')),None)
            if t is None or not all(finite(t.get(k)) for k in ('x','y','w','h','conf')) or not 0<=t['conf']<=1:
                raise ValueError('Visible detection not found')
            x=t['x']+t['w']/2;y=t['y']+t['h']/2;instance=self.source.get('stats',{}).get('instance')
            self.core.invalidate()
        if self.selection=='id':
            # The detector checks process, epoch and track under its own lock and selects
            # exactly that ID or nothing. A refusal leaves its current selection unchanged.
            if not instance:raise ValueError('Detector identity unknown; select again')
            query=urlencode({'id':tid,'epoch':epoch,'instance':instance})
            try:
                with urlopen(Request(self.camera+'/api/select_track?'+query,data=b'',method='POST'),timeout=1) as r:got=json.load(r)
            except HTTPError as e:
                if e.code==409:
                    try:reason=json.load(e).get('reason')
                    except Exception:reason=None
                    raise ValueError('Selection refused: '+str(reason or 'changed')+'; choose again') from None
                if e.code==404:raise ValueError('This detector has no select-by-ID route; start the bridge with --selection click') from None
                raise
            if got.get('ok') is not True or got.get('id')!=tid or got.get('epoch')!=epoch or got.get('instance')!=instance:
                raise ValueError('Detector confirmed a different selection; choose again')
        else:
            with urlopen(Request(self.camera+f'/api/mono_click?x={x}&y={y}',data=b'',method='POST'),timeout=1) as r:json.load(r)
        # The click route is coordinate-based and can pick another box. Never confirm a different ID.
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            time.sleep(.1)
            with self.lock:
                match=self.source.get('lock',{}).get('id')==tid and self.source.get('frame',{}).get('epoch')==epoch
            if match:
                if t.get('label')=='person' and t['conf']>=.5:self.select()
                return
        raise ValueError('Selection did not match; choose the person again')
    def select(self):
        with self.lock:
            s=dict(self.source)
            s['age_ms']=s.get('age_ms',10000)+(time.monotonic()-self.at)*1000
            if not (self.sim.get('armed') is False and self.sim.get('landed') is True and self.sim.get('telemetry_fresh') is True):
                raise ValueError('Simulator must be grounded')
            self.core.select(s,s.get('lock',{}).get('id'),s.get('frame',{}).get('epoch'))

PAGE=Path(__file__).with_name('fixed_camera.html').read_text()

def local_origin(headers, port):
    """Browser POSTs must come from this page on this loopback port. Comparing Origin with
    the request's own Host is not enough: another name resolving here would match itself."""
    return headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}') and headers.get('Origin')=='http://'+headers.get('Host','')

def handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        def reply(self,code,data,kind='application/json'):
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            u=urlsplit(self.path)
            if self.path=='/':self.reply(200,PAGE.encode(),'text/html; charset=utf-8')
            elif u.path=='/api/preview':
                if u.query:
                    # ?after=<version>: hold briefly until the box reading changes, so the page need not poll.
                    q=parse_qs(u.query,keep_blank_values=True)
                    try:
                        if set(q)!={'after'} or len(q['after'])!=1:raise ValueError
                        after=int(q['after'][0])
                    except ValueError:return self.reply(400,b'{}')
                    if bridge.box_reader:bridge.box_reader.wait_change(after,.25)
                data=bridge.depth_reader.snapshot(bridge.selection_key()) if bridge.depth_reader else {'depth_present':False,'reason':'Stereo reader not started'}
                if data.get('depth_backend')!='pi-sgbm':data['depth']=None;data['depth_present']=False
                if bridge.box_reader:
                    # Explicit mode: the shown number is the box reading; the full-map one is kept beside it for comparison.
                    data['person_depth_map']=data.get('person_depth');data['person_depth']=bridge.box_reader.snapshot(bridge.selection_key());data['version']=data['person_depth'].get('version')
                self.reply(200,json.dumps(data,allow_nan=False).encode())
            elif self.path=='/api/state':self.reply(200,json.dumps(bridge.snapshot(),allow_nan=False).encode())
            else:self.reply(404,b'{}')
        def do_POST(self):
            if self.path not in ('/api/select','/api/choose'):return self.reply(404,b'{}')
            if not local_origin(self.headers,self.server.server_port):return self.reply(403,b'Origin rejected')
            try:
                if self.path=='/api/choose':
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=1024:raise ValueError('Invalid selection request')
                    data=json.loads(self.rfile.read(size));bridge.choose(data['id'],data['epoch'])
                else:bridge.select()
                self.reply(200,b'{}')
            except (ValueError,TypeError,KeyError,OSError) as e:self.reply(409,str(e).encode(),'text/plain')
        def log_message(self,*args):pass
    return Handler

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--camera',default='http://10.42.0.128:8098');p.add_argument('--simulator',default='http://127.0.0.1:8099');p.add_argument('--calibration',type=Path);p.add_argument('--port',type=int,default=8103);p.add_argument('--seconds',type=float,default=600);p.add_argument('--depth-host');p.add_argument('--depth-port',type=int,default=18096);p.add_argument('--selection',choices=['click','id'],default='click',help='id needs a detector with /api/select_track; explicit, no fallback');p.add_argument('--detector-read',choices=['poll','wait'],default='poll',help='wait needs a detector whose /api/state accepts ?after=; explicit');p.add_argument('--person-depth',choices=['map','box'],default='map',help='box matches stereo only in the selected torso region via /box-depth; explicit, no fallback');p.add_argument('--depth-transport',choices=['pair','depth','depth-zlib'],default='pair',help='explicit; a missing route is an error, never a fallback')
    a=p.parse_args();b=Bridge(a.camera,a.simulator,json.loads(a.calibration.read_text()) if a.calibration else {},a.selection,a.detector_read)
    tunnel=None;depth_thread=None;box_thread=None
    if a.person_depth=='box' and not a.depth_host:p.error('--person-depth box needs --depth-host')
    if a.depth_host:
        from stereo_preview import StereoPreview
        tunnel=subprocess.Popen(['ssh','-N','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=5','-L',f'127.0.0.1:{a.depth_port}:127.0.0.1:8096',a.depth_host])
        b.depth_reader=StereoPreview(f'http://127.0.0.1:{a.depth_port}',b.person_depth,a.depth_transport)
        depth_thread=threading.Thread(target=b.depth_reader.run,daemon=True);depth_thread.start()
        if a.person_depth=='box':
            from stereo_preview import BoxDepth
            b.box_reader=BoxDepth(f'http://127.0.0.1:{a.depth_port}')
            box_thread=threading.Thread(target=b.box_reader.run,daemon=True);box_thread.start()
    server=ThreadingHTTPServer(('127.0.0.1',a.port),handler(b));worker=threading.Thread(target=b.run,daemon=True);worker.start()
    timer=threading.Timer(a.seconds,server.shutdown);timer.daemon=True;timer.start()
    import signal
    def stop_signal(*args):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_signal)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        b.stop.set();worker.join(2);server.server_close();timer.cancel()
        if b.depth_reader:b.depth_reader.stop.set();depth_thread.join(2)
        if b.box_reader:b.box_reader.stop.set();box_thread.join(2)
        if tunnel:tunnel.terminate();tunnel.wait(timeout=3)
if __name__=='__main__':main()
