"""PC receipt-time pose matching, an approximation to exposure-time alignment."""
from collections import deque
from dataclasses import dataclass
import math
from pathlib import Path

PC_BOOT_ID=Path('/proc/sys/kernel/random/boot_id').read_text().strip()

@dataclass(frozen=True)
class PoseMatch:
 position: tuple
 quaternion: tuple
 receipt_ns: int
 skew_ns: int

class PoseHistory:
 def __init__(self,max_skew_ns=50_000_000,horizon_ns=3_000_000_000):
  self.samples=deque(maxlen=600);self.max_skew_ns=max_skew_ns;self.horizon_ns=horizon_ns;self.source_time=None
 def add(self,receipt_ns,position,quaternion,source_time=None):
  if len(position)!=3 or len(quaternion)!=4 or not all(math.isfinite(float(v)) for v in [*position,*quaternion]):return False
  norm=math.sqrt(sum(float(v)**2 for v in quaternion))
  if norm<1e-9:return False
  if self.samples and receipt_ns<=self.samples[-1][0]:self.samples.clear()
  if source_time is not None and self.source_time is not None and source_time<self.source_time:self.samples.clear()
  self.source_time=source_time
  self.samples.append((receipt_ns,tuple(map(float,position)),tuple(float(v)/norm for v in quaternion)))
  while self.samples and receipt_ns-self.samples[0][0]>self.horizon_ns:self.samples.popleft()
  return True
 def match(self,frame,now_ns):
  stamp=frame.get('ingress_monotonic_ns')
  if frame.get('clock')!='pc-monotonic' or frame.get('clock_boot_id')!=PC_BOOT_ID:return None
  if type(stamp) is not int or not 0<=now_ns-stamp<=self.horizon_ns or not self.samples:return None
  when,pos,q=min(self.samples,key=lambda row:abs(row[0]-stamp))
  if abs(when-stamp)>self.max_skew_ns:return None
  return PoseMatch(pos,q,when,when-stamp)
