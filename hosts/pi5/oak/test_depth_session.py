import math
from pathlib import Path
import pytest
from depth_session import limit_reason

@pytest.mark.parametrize('flags',[1,2,4,8,15])
def test_current_flags_stop(flags):assert limit_reason(60,flags)=='active_limiting_flags'
def test_historical_flags_do_not_stop():assert limit_reason(60,0xe0000) is None
def test_limits():
 assert limit_reason(75,0,True)=='temperature_limit'
 assert limit_reason(75,0) is None
 assert limit_reason(78,0)=='temperature_limit'
 assert limit_reason(math.nan,0)=='unknown_temperature'
def test_session_restoration_is_systemd_owned():
 s=Path(__file__).with_name('run-cpu-depth-session.sh').read_text()
 assert 'RuntimeMaxSec=' in s and 'ExecStopPost=+/usr/bin/systemctl --no-block start garuda-oak-preview.service' in s
 assert 'Conflicts=garuda-oak-preview.service' in s
 assert 'systemctl stop garuda-mono' not in s

@pytest.mark.parametrize('failure',[False,True])
def test_supervisor_stops_child_on_limit_or_sensor_failure(monkeypatch,failure):
 import depth_session as session
 import sys,signal
 readings=iter([(60,0),(60,0),(78,0)])
 calls=[]
 def read():
  value=next(readings)
  if failure and value[0]==78:raise OSError('sensor unavailable')
  return value
 class Child:
  def poll(self):return None
  def send_signal(self,sig):calls.append(('signal',sig))
  def wait(self,timeout):calls.append(('wait',timeout));return 0
 monkeypatch.setattr(session,'thermal',read)
 monkeypatch.setattr(session.subprocess,'Popen',lambda *a,**k:Child())
 monkeypatch.setattr(session.signal,'signal',lambda *a:None)
 monkeypatch.setattr(session.time,'sleep',lambda _:None)
 monkeypatch.setattr(sys,'argv',['depth_session.py','--seconds','60'])
 session.main()
 assert calls==[('signal',signal.SIGINT),('wait',10)]

def test_map_interval_is_passed_to_the_owner(monkeypatch):
    import depth_session as session,sys
    seen=[]
    class Child:
        def poll(self):return 0
    monkeypatch.setattr(session,'thermal',lambda:(40.0,0))
    monkeypatch.setattr(session.subprocess,'Popen',lambda args,**k:seen.append(args) or Child())
    monkeypatch.setattr(sys,'argv',['depth_session.py','--seconds','5','--map-interval-ms','500'])
    session.main()
    assert seen[0][-2:]==['--map-interval-ms','500']
    monkeypatch.setattr(sys,'argv',['depth_session.py','--map-interval-ms','901'])
    import pytest
    with pytest.raises(SystemExit):session.main()
