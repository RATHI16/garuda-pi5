"""Read-only Gazebo truth recorder. Never supplies perception or control inputs."""
import threading,time

class Truth:
 def __init__(self):
  from gz.transport13 import Node
  from gz.msgs10.pose_v_pb2 import Pose_V
  self.lock=threading.Lock();self.value={};self.at=None;self.sim_ns=None;self.node=Node()
  if not self.node.subscribe(Pose_V,'/garuda/bench/truth',self.receive):raise RuntimeError('truth subscription failed')
 def receive(self,msg):
  values={p.name:dict(position=[p.position.x,p.position.y,p.position.z],quaternion=[p.orientation.w,p.orientation.x,p.orientation.y,p.orientation.z]) for p in msg.pose if p.name in ('walker','garuda_x500')}
  if len(values)!=2:return
  stamp=msg.header.stamp.sec*1000000000+msg.header.stamp.nsec
  with self.lock:
   if self.sim_ns is not None and stamp<=self.sim_ns:return
   self.value=values;self.at=time.monotonic();self.sim_ns=stamp
 def snapshot(self):
  with self.lock:
   if self.at is None:raise RuntimeError('actor/vehicle ground truth unavailable')
   result=dict(self.value,sim_ns=self.sim_ns,age_s=time.monotonic()-self.at)
  if result['age_s']>.5:raise RuntimeError('ground truth stale')
  return result
