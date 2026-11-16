"""Real loopback HTTP tests for the same handler used by the live vision service."""
import json,threading,time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
import pytest
import vision

@pytest.fixture
def server(monkeypatch):
 pixels=SimpleNamespace(lock=threading.RLock(),overview=None,snapshot=lambda:(None,None,(0,time.monotonic_ns()),None))
 monkeypatch.setattr(vision,'GazeboPixels',lambda:pixels)
 v=vision.Vision(None,{})
 http=vision.ThreadingHTTPServer(('127.0.0.1',0),vision.http_handler(v))
 t=threading.Thread(target=http.serve_forever,daemon=True);t.start()
 yield v,'http://127.0.0.1:'+str(http.server_port)
 http.shutdown();http.server_close();t.join()

def fetch(url):
 with urlopen(url,timeout=2) as r:return r.status,json.load(r) if r.status!=204 else None

def test_wait_wakes_on_new_frame_and_does_not_repeat(server):
 v,url=server;_,first=fetch(url+'/api/next');cursor=first['stream_cursor']
 entered=threading.Event();original=v.next_snapshot
 def observed(*args,**kwargs):entered.set();return original(*args,**kwargs)
 v.next_snapshot=observed
 with ThreadPoolExecutor() as pool:
  result=pool.submit(fetch,url+'/api/next?'+urlencode({'after':cursor}))
  assert entered.wait(1)
  with v.lock:
   v.state={'frame':{'seq':2}};v.last_at=time.monotonic();v.advance_revision()
  code,data=result.result(timeout=1)
 assert code==200 and data['frame']['seq']==2 and data['stream_cursor']!=cursor
 assert fetch(url+'/api/next?'+urlencode({'after':data['stream_cursor']}))[0]==204

def test_invalidation_and_process_restart_have_new_cursors(server):
 v,url=server;_,first=fetch(url+'/api/next')
 with v.lock:v.continuity_valid=True;v.invalidate('test outage')
 _,empty=fetch(url+'/api/next?'+urlencode({'after':first['stream_cursor']}))
 assert empty['age_ms'] is None and empty['error']=='test outage'
 _,value=fetch(url+'/api/next?after=previous-process:9999')
 assert value['stream_cursor']==empty['stream_cursor']

def test_invalid_cursor_queries_rejected(server):
 _,url=server
 for query in ['after=','after=a&after=b','unknown=x']:
  with pytest.raises(HTTPError) as e:fetch(url+'/api/next?'+query)
  assert e.value.code==400


def test_clip_list_uses_vision_instance_catalog(server):
 v,url=server
 assert fetch(url+'/api/clips')==(200,[])
 v.clips={'walk':{'label':'Walk example','path':'/not-exposed'}}
 assert fetch(url+'/api/clips')==(200,[{'id':'walk','label':'Walk example'}])

def test_existing_get_routes_after_handler_extraction(server,tmp_path):
 v,url=server
 assert fetch(url+'/api/state')[0]==200
 for route,code in [('/api/report',404),('/frame.jpg',400),('/frame.jpg?epoch=old&seq=1',410),('/overview.jpg',503),('/unknown',404)]:
  with pytest.raises(HTTPError) as e:fetch(url+route)
  assert e.value.code==code
 report=tmp_path/'report.json';report.write_text('{"result":"example"}');v.report=report
 assert fetch(url+'/api/report')==(200,{'result':'example'})
 v.frames[('camera',1)]=b'frame-jpeg'
 v.pixels.overview=(time.monotonic_ns(),b'overview-jpeg')
 for route,expected in [('/frame.jpg?epoch=camera&seq=1',b'frame-jpeg'),('/overview.jpg',b'overview-jpeg')]:
  with urlopen(url+route,timeout=2) as r:
   assert r.status==200 and r.headers['Content-Type']=='image/jpeg' and r.read()==expected
