"""The actual controller HTTP handler, without ROS or a vehicle."""
import json
import queue
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
import pytest
from control_lease import ControlLease
from sitl_demo import make_http_handler


@pytest.fixture
def api():
    lease=ControlLease(timeout=3.0);pending=queue.Queue();state={}
    args=SimpleNamespace(port=0,allow_origin=['http://pi:8098'])
    handler=make_http_handler(lambda:state,{'kind':'mono_estimate'},args,pending,lease)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path='/api/command', data=None, session=None, method='POST', origin='http://pi:8098'):
        headers={'Content-Type':'application/json','Origin':origin}
        if session is not None:headers['X-Garuda-Session']=session
        req=Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(data or {}).encode() if method=='POST' else None,headers=headers,method=method)
        try:r=urlopen(req,timeout=2)
        except HTTPError as e:r=e
        with r:
            raw=r.read();return r.status,dict(r.headers),json.loads(raw) if raw else None
    yield request,lease,pending,state
    server.shutdown();server.server_close();thread.join()


def test_preflight_and_queue_to_applied_ack(api):
    req,lease,pending,state=api
    status,headers,_=req('/api/heartbeat',method='OPTIONS')
    assert status==204 and 'X-Garuda-Session' in headers['Access-Control-Allow-Headers']
    status,_,reply=req(data={'command':'hold'},session='tab')
    assert status==202 and reply['queued'] and reply['id']
    assert lease.state(time.monotonic())['last_command'] is None
    called=[];lease.execute(pending.get_nowait(),time.monotonic(),lambda a,n:called.append(a))
    state['control']=lease.state(time.monotonic())
    status,_,body=req('/api/state',method='GET')
    assert status==200 and body['control']['last_command']['id']==reply['id']
    assert body['control']['last_command']['applied'] and called==[{'command':'hold'}]


def test_competing_tab_heartbeat_and_expired_queue(api):
    req,lease,pending,state=api
    assert req(data={'command':'follow'},session='a')[0]==202
    assert req(data={'command':'follow'},session='b')[0]==400
    assert req('/api/heartbeat',session='b')[0]==400
    assert req('/api/heartbeat',session='a')[0]==200
    ticket=pending.get_nowait()
    # Advance the lease deadline explicitly; avoid a timing-dependent sleep in the test.
    with lease.lock:lease.deadline=time.monotonic()-1
    assert req('/api/heartbeat',session='a')[0]==400
    called=[];lease.execute(ticket,time.monotonic(),lambda *a:called.append(a))
    assert not called and lease.consume_disconnect(time.monotonic())
    assert req(data={'command':'hold'},session='b')[0]==202


def test_unallowed_origin_and_malformed_body_do_not_queue(api):
    req,lease,pending,state=api
    assert req(data={'command':'follow'},session='a',origin='http://untrusted')[0]==403
    assert pending.empty() and lease.owner is None


def test_live_source_requires_named_lease_even_before_any_browser(api):
    req,lease,pending,state=api
    assert req(data={'command':'source','kind':'pi_onboard'})[0]==400
    assert pending.empty()
    assert req(data={'command':'source','kind':'pi_onboard'},session='live-tab')[0]==202
