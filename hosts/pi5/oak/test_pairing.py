import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1]))
from pairing import Pairing, Frame
from oak_pair import parse_bytes


def frame(seq, stamp, channels):
    return Frame(seq,stamp,stamp,bytes(2*2*channels),stamp)


def test_pair_roundtrip_cursor_epoch_and_omission():
    p=Pairing('boot','cal',(2,2))
    p.add('rgb',frame(10,1_000_000_000,3))
    p.add('depth',frame(20,1_002_000_000,2))
    p.advance(1_003_000_000)
    code,body=p.response(now_ns=1_004_000_000)
    pair=parse_bytes(body,(2,2))
    assert code==200 and pair.meta['pair_skew_ns']==2_000_000
    assert p.response(0,p.epoch,now_ns=1_004_000_000)[0]==204
    assert p.response(500,'old',now_ns=1_004_000_000)[0]==200
    _,body=p.response(want_depth=False,now_ns=1_004_000_000)
    assert parse_bytes(body,(2,2)).meta['depth_reason']=='omitted_by_request'


def test_missing_depth_bounded_and_stale_not_reused():
    p=Pairing('boot','cal',(2,2))
    p.add('rgb',frame(1,1_000_000_000,3))
    p.advance(1_079_000_000)
    assert p.latest is None
    p.advance(1_080_000_000)
    code,body=p.response(now_ns=1_081_000_000)
    assert code==200 and parse_bytes(body,(2,2)).depth_mm is None
    assert p.response(now_ns=2_000_000_000)[0]==503


def test_skew_rejection_and_bounded_queues():
    p=Pairing('boot','cal',(2,2))
    p.add('depth',frame(1,1_100_000_000,2))
    p.add('rgb',frame(1,1_000_000_000,3))
    p.advance(1_080_000_000)
    _,body=p.response(now_ns=1_081_000_000)
    assert parse_bytes(body,(2,2)).meta['depth_reason']=='skew_exceeded'
    for i in range(10): p.add('rgb',frame(i,2_000_000_000+i,3))
    assert len(p.rgb)==4 and p.stats['rgb_overwrites']==6


def test_only_old_depth_means_wait_expired_not_bad_sync():
    p=Pairing('boot','cal',(2,2))
    p.add('depth',frame(1,900_000_000,2))
    p.add('rgb',frame(2,1_000_000_000,3))
    p.advance(1_080_000_000)
    _,body=p.response(now_ns=1_081_000_000)
    assert parse_bytes(body,(2,2)).meta['depth_reason']=='pair_wait_expired'

def test_unqualified_depth_is_preview_only():
 import json,struct
 p=Pairing('boot','cal',size=(1,1));p.measurement_qualified=False
 now=1_000_000_000
 p.add('rgb',Frame(1,now,now,b'abc',now));p.add('depth',Frame(1,now,now,b'\x01\x00',now));p.advance(now)
 for preview,valid in [(False,False),(True,True)]:
  status,body=p.response(now_ns=now,preview=preview)
  n=struct.unpack('>I',body[:4])[0];m=json.loads(body[4:4+n])
  assert m['depth_valid']==valid and m['measurement_qualified'] is False
  assert m['depth']['length']==(2 if preview else 0)

def test_preview_parser_requires_explicit_opt_in():
 import io,pytest
 from oak_pair import read_pair,PairError
 p=Pairing('boot','cal',size=(1,1));p.measurement_qualified=False;now=10**9
 p.add('rgb',Frame(1,now,now,b'abc',now));p.add('depth',Frame(1,now,now,b'\x01\x00',now));p.advance(now)
 _,body=p.response(now_ns=now,preview=True)
 with pytest.raises(PairError,match='unqualified'):read_pair(io.BytesIO(body),len(body),(1,1))
 pair=read_pair(io.BytesIO(body),len(body),(1,1),allow_unqualified_preview=True)
 assert pair.depth_mm[0,0]==1

def test_rgb_only_publication_does_not_wait_for_depth():
 import json,struct
 p=Pairing('boot','cal',size=(1,1));p.measurement_qualified=False
 now=10**9
 for seq in range(5):p.publish_rgb(Frame(seq,now,now,b'abc',now))
 code,b=p.response(now_ns=now)
 n=struct.unpack('>I',b[:4])[0];m=json.loads(b[4:4+n])
 assert code==200 and m['rgb']['seq']==4 and m['pair_id']==4 and not m['depth_valid']
