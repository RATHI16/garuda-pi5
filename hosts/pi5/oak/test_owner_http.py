"""Real HTTP checks against an owner with synthetic acquisition and no SDK/device."""
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from pairing import Pairing, Frame


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setitem(sys.modules,'depthai',types.ModuleType('depthai'))
    spec=importlib.util.spec_from_file_location('owner_under_test',Path(__file__).with_name('preview.py'))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    server=mod.ThreadingHTTPServer(('127.0.0.1',0),mod.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield mod,'http://127.0.0.1:'+str(server.server_port)
    server.shutdown();server.server_close();thread.join()


def test_wait_uses_published_limit(owner):
    mod,base=owner
    mod.pairing=Pairing('boot','cal',(2,2))
    mod.pairing.limits['pair_wait_ms']=20
    now=time.monotonic_ns()
    mod.pairing.add('rgb',Frame(1,now,now,bytes(12),now))
    mod.pairing.add('depth',Frame(1,now,now,bytes(8),now))
    mod.pairing.advance(now)
    start=time.monotonic()
    with urlopen(base+'/pair?after=0&epoch='+mod.pairing.epoch) as r:
        assert r.status==204
    assert time.monotonic()-start<.5  # would be ~1 second (stale) with old hard-coded wait


def test_pair_unavailable_with_stereo_disabled(owner):
    _,base=owner
    with pytest.raises(HTTPError) as exc:
        urlopen(base+'/pair')
    assert exc.value.code==503


def test_imu_disabled_then_epoch_cursor_http(owner):
    import json
    from imu_stream import IMUHistory
    mod,base=owner
    with pytest.raises(HTTPError) as exc: urlopen(base+'/imu')
    assert exc.value.code==503
    mod.imu_history=IMUHistory('new')
    with urlopen(base+'/imu?epoch=old&after=99') as r:
        data=json.load(r)
    assert data['epoch_changed'] and data['epoch']=='new'
    assert data['samples']==[]
    with pytest.raises(HTTPError) as exc:urlopen(base+'/imu?after=-5')
    assert exc.value.code==400


def test_depth_preview_route_serves_depth_only_and_checks_query(owner):
    import io,zlib,numpy as np
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pc'))
    from stereo_preview import read_depth_preview
    mod,base=owner
    mod.pairing=Pairing('boot','cal',(2,2));mod.pairing.measurement_qualified=False
    mod.pairing.limits['pair_wait_ms']=20
    now=time.monotonic_ns();depth=np.array([[0,1500],[2500,0]],'<u2')
    mod.pairing.add('rgb',Frame(1,now,now,bytes(12),now))
    mod.pairing.add('depth',Frame(1,now,now,depth.tobytes(),now))
    mod.pairing.advance(now)
    for enc in ('raw','zlib1'):
        with urlopen(base+'/depth-preview?enc='+enc) as r:
            assert r.headers['Content-Type']=='application/vnd.garuda.oak-depth-preview'
            body=r.read();pair=read_depth_preview(io.BytesIO(body),int(r.headers['Content-Length']),(2,2))
        assert np.array_equal(pair.depth_mm,depth) and pair.meta['depth_encoding']==enc
    with urlopen(base+'/depth-preview?after=0&epoch='+mod.pairing.epoch) as r:assert r.status==204
    for bad in ('enc=lz4','depth=1','enc=raw&enc=zlib1'):
        with pytest.raises(HTTPError) as exc:urlopen(base+'/depth-preview?'+bad)
        assert exc.value.code==400
    with pytest.raises(HTTPError) as exc:urlopen(base+'/pair?enc=raw')
    assert exc.value.code==400
    # The normal pair route is unchanged and still withholds unqualified depth.
    with urlopen(base+'/pair') as r:
        from oak_pair import parse_bytes
        assert parse_bytes(r.read(),(2,2)).meta['depth_valid'] is False


def test_box_depth_route_envelope_and_refusals(owner):
    import json,struct
    mod,base=owner;server=None
    # Without a host-stereo capture there is no box route behind it.
    with pytest.raises(HTTPError) as e:urlopen(base+'/box-depth?epoch=E&rgb_host_ns=5&x0=0&y0=0&x1=2&y1=2')
    assert e.value.code==503
    for bad in ['/box-depth','/box-depth?epoch=E&rgb_host_ns=5&x0=0&y0=0&x1=2','/box-depth?epoch=E&rgb_host_ns=x&x0=0&y0=0&x1=2&y1=2',
                '/box-depth?epoch=E&rgb_host_ns=5&x0=0&y0=0&x1=2&y1=2&extra=1','/box-depth?epoch=&rgb_host_ns=5&x0=0&y0=0&x1=2&y1=2']:
        with pytest.raises(HTTPError) as e:urlopen(base+bad)
        assert e.value.code==400,bad
    import gc
    server=next(o for o in gc.get_objects() if isinstance(o,mod.ThreadingHTTPServer) and base.endswith(str(o.server_port)))
    calls=[]
    def compute(epoch,stamp,roi):
        calls.append((epoch,stamp,roi))
        return (200,dict(schema='garuda.oak-box-depth.1',roi=list(roi)),b'\x01\x00'*4) if epoch=='E' else (409,dict(reason='epoch_changed'),b'')
    server.box_depth=compute
    with urlopen(base+'/box-depth?epoch=E&rgb_host_ns=5&x0=0&y0=0&x1=2&y1=2') as r:body=r.read();assert int(r.headers['Content-Length'])==len(body)
    (n,)=struct.unpack('>I',body[:4]);assert json.loads(body[4:4+n])['roi']==[0,0,2,2] and body[4+n:]==b'\x01\x00'*4
    assert calls==[('E',5,(0,0,2,2))]
    with pytest.raises(HTTPError) as e:urlopen(base+'/box-depth?epoch=F&rgb_host_ns=5&x0=0&y0=0&x1=2&y1=2')
    body=e.value.read();(n,)=struct.unpack('>I',body[:4])
    assert e.value.code==409 and json.loads(body[4:4+n])=={'reason':'epoch_changed'}
