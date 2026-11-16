"""Bounded thermal sampling; a failed read never falls back to an old good value."""
import time

class ThermalCache:
 def __init__(self,read,clock=time.monotonic,interval=1.,max_age=1.5):
  self.read=read;self.clock=clock;self.interval=interval;self.max_age=max_age
  self.at=None;self.value=None;self.error=None;self.reads=0
 def get(self):
  now=self.clock()
  if self.at is None or now-self.at>=self.interval:
   self.at=now;self.value=None;self.error=None;self.reads+=1
   try:self.value=self.read()
   except Exception as e:self.error=str(e)
  age=self.clock()-self.at
  if self.error or self.value is None or not 0<=age<=self.max_age:
   raise RuntimeError('thermal state unavailable or stale: '+str(self.error))
  return self.value
 def snapshot(self):
  return dict(sample_monotonic=self.at,age_s=None if self.at is None else self.clock()-self.at,
              temperature_c=self.value[0] if self.value else None,flags=self.value[1] if self.value else None,
              error=self.error,reads=self.reads)
