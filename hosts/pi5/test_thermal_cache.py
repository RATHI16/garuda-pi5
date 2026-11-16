import pytest
from thermal_cache import ThermalCache

def test_repeated_checks_sample_only_once_per_second():
 clock=[0.];reads=[]
 c=ThermalCache(lambda:(reads.append(clock[0]) or (60,0)),clock=lambda:clock[0])
 for t in [0,.01,.02,.4,.99,1.,1.01]:clock[0]=t;assert c.get()==(60,0)
 assert reads==[0,1.] and c.reads==2

def test_error_does_not_reuse_good_sample_or_retry_every_frame():
 clock=[0.];n=[0]
 def read():
  n[0]+=1
  if n[0]>1:raise OSError('unreadable')
  return 60,0
 c=ThermalCache(read,clock=lambda:clock[0]);c.get();clock[0]=1.
 with pytest.raises(RuntimeError):c.get()
 clock[0]=1.1
 with pytest.raises(RuntimeError):c.get()
 assert n[0]==2 and c.snapshot()['temperature_c'] is None

def test_slow_read_and_clock_regression_fail_closed():
 clock=[0.]
 def read():clock[0]=2.;return 60,0
 c=ThermalCache(read,clock=lambda:clock[0])
 with pytest.raises(RuntimeError):c.get()
 clock[0]=-.1
 with pytest.raises(RuntimeError):c.get()
