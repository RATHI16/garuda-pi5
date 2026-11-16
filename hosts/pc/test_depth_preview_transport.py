"""Depth-only preview transport: Pi serializer against the PC reader, offline."""
import io,sys,zlib
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi5'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi5'/'oak'))
from pairing import Pairing,Frame,pack_depth_preview
from oak_pair import PairError,read_pair
from stereo_preview import StereoPreview,read_depth_preview

W,H=4,2
def source(qualified=False):
    p=Pairing('boot','cal',(W,H));p.measurement_qualified=qualified
    depth=np.array([[0,1500,0,2500],[0,0,3000,0]],'<u2')
    p.add('rgb',Frame(10,1_000_000_000,1_000_000_000,bytes(W*H*3),1_000_000_000))
    p.add('depth',Frame(20,1_002_000_000,1_002_000_000,depth.tobytes(),1_002_000_000))
    p.advance(1_003_000_000);return p,depth
def get(p,**k):return p.response(now_ns=1_004_000_000,**k)

@pytest.mark.parametrize('encoding',['raw','zlib1'])
def test_roundtrip_is_lossless_and_keeps_pair_identity(encoding):
    p,depth=source();code,(meta,payload)=get(p,preview=True,omit_rgb=True)
    body=pack_depth_preview(meta,payload,encoding)
    pair=read_depth_preview(io.BytesIO(body),len(body),(W,H))
    assert code==200 and pair.rgb==b'' and np.array_equal(pair.depth_mm,depth)
    full=read_pair(io.BytesIO(get(p,preview=True)[1]),len(get(p,preview=True)[1]),(W,H),allow_unqualified_preview=True)
    assert pair.key==full.key and pair.meta['rgb']['capture_host_ns']==full.meta['rgb']['capture_host_ns']
    assert pair.meta['pair_skew_ns']==full.meta['pair_skew_ns'] and pair.meta['measurement_qualified'] is False

def test_cursor_still_applies():
    p,_=source()
    assert get(p,after=0,epoch=p.epoch,preview=True,omit_rgb=True)[0]==204

def test_not_served_without_preview_or_for_qualified_depth():
    p,_=source();assert get(p,omit_rgb=True)==(503,None)
    q,_=source(qualified=True);assert get(q,preview=True,omit_rgb=True)==(503,None)

def test_pair_consumer_rejects_depth_only_body():
    p,_=source();_,(meta,payload)=get(p,preview=True,omit_rgb=True);body=pack_depth_preview(meta,payload,'raw')
    with pytest.raises(PairError):read_pair(io.BytesIO(body),len(body),(W,H),allow_unqualified_preview=True)

def test_reader_rejects_full_pair_and_bad_payloads():
    p,_=source();full=get(p,preview=True)[1]
    with pytest.raises(PairError):read_depth_preview(io.BytesIO(full),len(full),(W,H))
    _,(meta,payload)=get(p,preview=True,omit_rgb=True)
    for body in [pack_depth_preview(meta,payload,'raw')[:-1],pack_depth_preview(meta,payload,'zlib1')+b'x']:
        with pytest.raises(PairError):read_depth_preview(io.BytesIO(body),len(body),(W,H))
    import json,struct
    def forged(change,data):
        m=dict(meta,depth_encoding='zlib1',depth_encoded_length=len(data));change(m);blob=json.dumps(m).encode()
        return struct.pack('>I',len(blob))+blob+data
    bomb=zlib.compress(bytes(W*H*2*50),1);short=zlib.compress(bytes(3),1);junk=b'not zlib data'
    cases=[forged(lambda m:None,bomb),forged(lambda m:None,short),forged(lambda m:None,junk),
           forged(lambda m:m.update(measurement_qualified=True),zlib.compress(payload,1)),
           forged(lambda m:m.update(preview_only=False),zlib.compress(payload,1)),
           forged(lambda m:m.update(depth_encoding='lz4'),payload)]
    for body in cases:
        with pytest.raises(PairError):read_depth_preview(io.BytesIO(body),len(body),(W,H))

def test_unknown_encoding_refused_by_serializer():
    p,_=source();_,(meta,payload)=get(p,preview=True,omit_rgb=True)
    with pytest.raises(ValueError):pack_depth_preview(meta,payload,'lz4')

def test_transport_is_explicit_in_the_request_and_health():
    assert StereoPreview('',transport='depth-zlib').query()=='/depth-preview?enc=zlib1'
    r=StereoPreview('',transport='depth');r.key=('raw',3)
    assert r.query()=='/depth-preview?enc=raw&after=3&epoch=raw' and r.snapshot()['transport']=='depth'
    with pytest.raises(KeyError):StereoPreview('',transport='auto')
