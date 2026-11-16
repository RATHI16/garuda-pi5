import io,json
from types import SimpleNamespace
import pytest
import observation_client as client
from pose_history import PC_BOOT_ID

def response(data,status=200):
 r=io.BytesIO(json.dumps(data).encode());r.status=status;return r

def payload(cursor='process:1'):
 return dict(stream_cursor=cursor,age_ms=5,frame=dict(ingress_monotonic_ns=100_240_000_000,clock='pc-monotonic',clock_boot_id=PC_BOOT_ID))

def test_server_wait_is_not_added_to_fresh_frame_age(monkeypatch):
 monkeypatch.setattr(client,'time',SimpleNamespace(monotonic=lambda:100.,monotonic_ns=lambda:100_250_000_000))
 monkeypatch.setattr(client,'urlopen',lambda *a,**k:response(payload()))
 r=client.ObservationClient();d=r.read(dict(kind='gazebo',generation=1,url='http://localhost'))
 assert d['age_ms']==10  # 250ms request duration must not become image age

def test_producer_stale_override_and_unknown_age_preserved(monkeypatch):
 monkeypatch.setattr(client,'time',SimpleNamespace(monotonic=lambda:100.,monotonic_ns=lambda:100_250_000_000))
 for reported in [1e9,None]:
  p=payload();p['age_ms']=reported
  monkeypatch.setattr(client,'urlopen',lambda *a,**k:response(p))
  assert client.ObservationClient().read(dict(kind='gazebo',generation=1,url='http://localhost'))['age_ms']==reported

def test_generation_resets_cursor_and_204_delivers_nothing(monkeypatch):
 urls=[];replies=iter([response(payload()),response({},204),response(payload('new:1'))])
 def open(url,**kwargs):urls.append(url);return next(replies)
 monkeypatch.setattr(client,'urlopen',open)
 r=client.ObservationClient();s=dict(kind='gazebo',generation=1,url='http://localhost')
 r.read(s);assert r.read(s) is None
 r.read(dict(s,generation=2))
 assert '?after=' in urls[1] and '?after=' not in urls[2]

def test_clock_mismatch_does_not_advance_cursor(monkeypatch):
 p=payload();p['frame']['clock_boot_id']='other'
 monkeypatch.setattr(client,'urlopen',lambda *a,**k:response(p))
 r=client.ObservationClient()
 with pytest.raises(ValueError):r.read(dict(kind='gazebo',generation=1,url='http://localhost'))
 assert r.cursor is None
