"""Distinct producer-result counters at the reader, controller and evaluator."""
import threading,uuid

class DeliveryCounts:
 def __init__(self):
  self.lock=threading.Lock();self.instance=uuid.uuid4().hex
  self.counts=dict(reader_frames=0,controller_frames=0,evaluated_frames=0,queue_overwrites=0)
  self.last={};self.producer=None
 def frame(self,stage,generation,data):
  f=data.get('frame',{});epoch=f.get('epoch');seq=f.get('seq')
  if not isinstance(epoch,str) or type(seq) is not int:return
  key=(generation,epoch,seq)
  with self.lock:
   if self.last.get(stage)!=key:self.counts[stage]+=1;self.last[stage]=key
   if stage=='reader_frames':self.producer=dict(generation=generation,epoch=epoch,seq=seq,processed=data.get('metrics',{}).get('frames'))
 def overwrite(self):
  with self.lock:self.counts['queue_overwrites']+=1
 def snapshot(self):
  with self.lock:return dict(instance=self.instance,**self.counts,last={k:list(v) for k,v in self.last.items()},producer=self.producer)
