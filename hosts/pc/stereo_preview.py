"""Bounded host-stereo display reader; never supplies control measurements."""
import base64
from collections import Counter, deque
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

MAX_AGE_MS=700
TRANSPORTS={'pair':'/pair-preview?depth=1','depth':'/depth-preview?enc=raw','depth-zlib':'/depth-preview?enc=zlib1'}

def read_depth_preview(stream,content_length,expect_size=(1280,720)):
    """Read one /depth-preview body: pair metadata, no RGB bytes, depth raw or zlib.
    Display only. Every /pair metadata rule still applies except the RGB payload length."""
    import copy,json,struct,zlib
    import numpy as np
    from oak_pair import Pair,PairError,validate_meta,MAX_METADATA_BYTES
    def need(ok,msg):
        if not ok:raise PairError(msg)
    def exact(n,what):
        data=stream.read(n);need(len(data)==n,'truncated '+what);return data
    (n,)=struct.unpack('>I',exact(4,'length prefix'));need(0<n<=MAX_METADATA_BYTES,'metadata length out of range')
    try:meta=json.loads(exact(n,'metadata').decode('utf-8'))
    except (UnicodeDecodeError,json.JSONDecodeError):raise PairError('metadata is not valid UTF-8 JSON') from None
    need(isinstance(meta,dict) and isinstance(meta.get('rgb'),dict),'missing rgb block')
    need(meta.get('rgb_omitted') is True and meta['rgb'].get('length')==0,'not a depth-only preview')
    need(meta.get('preview_only') is True and meta.get('measurement_qualified') is False,'depth-only transport is preview only')
    w,h=expect_size;check=copy.deepcopy(meta);check['rgb']['length']=w*h*3
    validate_meta(check,expect_size);need(meta['depth_valid'] is True,'depth-only preview without depth')
    size=meta.get('depth_encoded_length');encoding=meta.get('depth_encoding')
    need(type(size) is int and 0<size<=w*h*2+65536 and content_length==4+n+size,'bad encoded depth length')
    raw=exact(size,'depth payload');need(stream.read(1)==b'','trailing bytes')
    if encoding=='zlib1':
        d=zlib.decompressobj()
        try:raw=d.decompress(raw,w*h*2+1)
        except zlib.error:raise PairError('depth payload is not valid zlib') from None
        need(d.eof and not d.unconsumed_tail and not d.unused_data,'depth payload has extra or incomplete data')
    else:need(encoding=='raw','unsupported depth encoding')
    need(len(raw)==w*h*2,'depth size does not match the image')
    return Pair(meta,b'',np.frombuffer(raw,dtype='<u2').reshape(h,w).astype(np.uint16,copy=False))

def read_box_depth(stream,content_length,region):
    """The /box-depth answer reader lives with the Pi-side consumer; one implementation."""
    pi=str(Path(__file__).resolve().parents[1]/'pi5')
    if pi not in sys.path:sys.path.insert(0,pi)
    from oak_box import read_box_depth as read
    return read(stream,content_length,region)

class BoxDepth:
    """Stereo matched only in the selected person's torso region, one request per
    detector result. Display only; same 700 ms age limit and selection check as the
    full-map reading. No fallback: a refused or failed request shows as no reading."""
    def __init__(self,url):
        self.url=url;self.cv=threading.Condition();self.job=None;self.stop=threading.Event()
        self.reading=None;self.reading_at=0;self.reason='Waiting for a selected person'
        self.counts=Counter();self.error=None;self.last_frame=None;self.request_ms=deque(maxlen=200);self.times=deque(maxlen=60)
        self.version=0
    def bump(self):
        """Caller holds the lock. Wakes readers waiting for a change in what would be shown."""
        self.version+=1;self.cv.notify_all()
    def wait_change(self,after,timeout):
        with self.cv:self.cv.wait_for(lambda:self.version!=after,timeout)
    def submit(self,job):
        """Latest job only. `job` is None when no visible person is selected."""
        with self.cv:
            if job is None:
                if self.reason!='Select a visible person':self.reason='Select a visible person';self.bump()
                return False
            if job['frame']==self.last_frame:return False
            self.last_frame=job['frame']
            if self.job is not None:self.counts['superseded']+=1
            self.job=job;self.counts['submitted']+=1;self.cv.notify();return True
    def step(self,job,opener=urlopen):
        from urllib.error import HTTPError
        from stereo_person_preview import region_stat
        import json,struct
        if job['age_ms']+(time.monotonic()-job['at'])*1000>=MAX_AGE_MS:
            with self.cv:self.counts['stale_before_request']+=1;self.reason='Matching detection is stale';self.bump()
            return
        x0,y0,x1,y1=job['region'];started=time.monotonic()
        query=f"/box-depth?epoch={job['owner_epoch']}&rgb_host_ns={job['capture']}&x0={x0}&y0={y0}&x1={x1}&y1={y1}"
        try:
            with opener(self.url+query,timeout=1) as r:meta,patch=read_box_depth(r,int(r.headers['Content-Length']),job['region'])
        except HTTPError as e:
            try:
                body=e.read();(n,)=struct.unpack('>I',body[:4]);reason=str(json.loads(body[4:4+n]).get('reason'))
            except Exception:reason='HTTP '+str(e.code)
            with self.cv:self.counts['refused: '+reason]+=1;self.reason='Stereo for this frame refused: '+reason;self.bump()
            return
        except Exception as e:
            with self.cv:self.counts['errors']+=1;self.error=type(e).__name__+': '+str(e);self.reason='Box stereo unavailable';self.bump()
            return
        took=(time.monotonic()-started)*1000;result=region_stat(patch,job['region'])
        with self.cv:
            self.error=None;self.request_ms.append(took);self.reason=result.get('reason','');self.bump()
            if result.get('z_m') is None:self.counts['answer_without_reading']+=1;return
            result.update(selection_key=job['key'],age_ms=job['age_ms'],method='box',request_ms=took,
                pi_compute_ms=meta.get('compute_ns',0)/1e6,rgb_depth_skew_ms=meta['rgb_span_ns']/1e6,strip=meta.get('strip'))
            self.reading=result;self.reading_at=job['at'];self.counts['readings']+=1;self.times.append(time.monotonic())
    def run(self):
        while not self.stop.is_set():
            with self.cv:
                self.cv.wait_for(lambda:self.job is not None,timeout=.2)
                job=self.job;self.job=None
            if job is not None:self.step(job)
    def snapshot(self,selection_key=None):
        with self.cv:
            d=dict(self.reading or {})
            age=d.get('age_ms',10000)+(time.monotonic()-self.reading_at)*1000
            if selection_key is not None and d.get('selection_key')==selection_key and age<MAX_AGE_MS:d['age_ms']=age
            else:d={'reason':'Last box stereo reading expired' if self.reason.startswith('Median valid') else self.reason,'method':'box'}
            ms=sorted(self.request_ms)
            d['version']=self.version
            d['health']=dict(counts=dict(self.counts),error=self.error,request_ms_median=ms[len(ms)//2] if ms else None,
                readings_per_s=(len(self.times)-1)/(self.times[-1]-self.times[0]) if len(self.times)>1 and self.times[-1]>self.times[0] else None)
            return d

class StereoPreview:
    def __init__(self,url,person_depth=None,transport='pair'):
        self.url=url;self.path=TRANSPORTS[transport];self.transport=transport;self.stop=threading.Event();self.lock=threading.RLock()
        self.person_depth=person_depth;self.distance=None;self.distance_at=0;self.person_reason="Waiting for matching person stereo pixels"
        self.value={};self.at=0;self.key=None;self.times=deque(maxlen=60)
        # Latest pair is kept so association can be retried without another transfer.
        self.pair=None;self.pair_at=0;self.pair_done=True
        self.counts=Counter();self.error=None
    def snapshot(self,selection_key=None):
        with self.lock:
            health=dict(reader_error=self.error,counts=dict(self.counts),transport=self.transport)
            if time.monotonic()-self.at>1:return dict(health,depth_present=False,reason='Stereo frames stale or unavailable')
            out=dict(self.value,**health)
            d=dict(self.distance or {})
            age=d.get('age_ms',10000)+(time.monotonic()-self.distance_at)*1000
            if selection_key is not None and d.get('selection_key')==selection_key and age<MAX_AGE_MS:
                d['age_ms']=age;out['person_depth']=d
            else:out['person_depth']={'reason':'Last matching stereo reading expired' if self.person_reason.startswith('Median valid') else self.person_reason}
            return out
    def query(self):
        """Cursor request: the owner long-polls and answers 204 when no newer pair exists."""
        q=self.path
        if self.key is not None:q+=f'&after={self.key[1]}&epoch={self.key[0]}'
        return q
    def close_pair(self):
        """Count one final outcome for the cached pair. Caller holds the lock."""
        if self.pair is not None and not self.pair_done:
            self.counts['pair_without_reading: '+self.person_reason]+=1
        self.pair_done=True
    def associate(self):
        """Try the cached pair against detector history. Safe from any thread; one
        reading at most per pair, and never against a pair older than the age limit."""
        with self.lock:
            if self.pair is None or self.pair_done or self.person_depth is None:return
            if (time.monotonic()-self.pair_at)*1000>=MAX_AGE_MS:self.close_pair();return
            person=self.person_depth(self.pair.meta,self.pair.depth_mm)
            self.person_reason=person.get('reason','Waiting for matching detection')
            if person.get('z_m') is not None:
                self.distance=person;self.distance_at=time.monotonic()
                self.counts['pair_with_reading']+=1;self.pair_done=True
    def accept(self,pair,render):
        """Take one newly received pair. Returns False for a repeat of the cached pair."""
        if pair.meta.get('depth_backend')!='pi-sgbm' or pair.depth_mm is None:raise ValueError('Host stereo unavailable')
        key=pair.key
        with self.lock:
            if key==self.key:self.counts['duplicate_pair']+=1;return False
            self.close_pair()
            now=time.monotonic()
            if self.key is not None and key[0]!=self.key[0]:self.times.clear()
            self.key=key;self.times.append(now);self.pair=pair;self.pair_at=now;self.pair_done=False
            self.counts['pairs']+=1
        image,valid=render(pair.depth_mm)
        fps=(len(self.times)-1)/(now-self.times[0]) if len(self.times)>1 else None
        with self.lock:
            self.at=now;self.error=None
            self.value={'depth_present':True,'depth_backend':'pi-sgbm','depth':image,'valid_fraction':valid,'preview_fps':fps,'sequence':key[1],'measurement_qualified':False}
        self.associate()
        return True
    def fail(self,error):
        with self.lock:
            self.error=type(error).__name__+': '+str(error);self.counts['read_errors']+=1
            self.value={'depth_present':False,'reason':'Host stereo unavailable'}
    def run(self):
        try:
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi5'))
            import cv2
            import numpy as np
            from oak_pair import read_pair
            from oak_calibration import depth_preview
        except Exception as e:
            # A wrong interpreter must be visible in /api/preview, not a silent dead thread.
            with self.lock:self.error='Reader cannot start: '+type(e).__name__+': '+str(e)
            return
        def render(depth):
            image=cv2.resize(depth_preview(depth),(640,360),interpolation=cv2.INTER_NEAREST)
            ok,jpg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,75])
            if not ok:raise ValueError('Encoding failed')
            return 'data:image/jpeg;base64,'+base64.b64encode(jpg).decode(),float(np.count_nonzero(depth)/depth.size)
        while not self.stop.is_set():
            try:
                # Timeout exceeds the owner's 2 s long-poll so an idle wait is not an error.
                with urlopen(self.url+self.query(),timeout=3) as r:
                    size=int(r.headers['Content-Length'])
                    pair=None if r.status==204 else read_pair(r,size,allow_unqualified_preview=True) if self.transport=='pair' else read_depth_preview(r,size)
                    if pair is not None:
                        with self.lock:self.counts['bytes']+=size
                if pair is None:
                    with self.lock:self.counts['no_new_pair']+=1
                else:self.accept(pair,render)
                self.associate()
                self.stop.wait(.005)
            except Exception as e:
                self.fail(e);self.stop.wait(.2)
