"""Completed inference telemetry; optional per-frame journal events, no model changes."""
import json,os,time,uuid,threading

class Progress:
    def __init__(self):
        self.instance=uuid.uuid4().hex;self.count=0;self.last=None;self.lock=threading.Lock()
    def record(self,seq,epoch,capture_ns,received_ns,start_ns):
        row=dict(kind='garuda_inference',instance=self.instance,seq=seq,epoch=epoch,
                 capture_host_ns=capture_ns,received_host_ns=received_ns,start_host_ns=start_ns,done_host_ns=time.monotonic_ns())
        with self.lock:self.count+=1;self.last=row;row['completed']=self.count
        if os.environ.get('GARUDA_MONO_TIMING')=='1':print(json.dumps(row),flush=True)
    def snapshot(self):
        with self.lock:return dict(instance=self.instance,completed=self.count,last=dict(self.last) if self.last else None)
