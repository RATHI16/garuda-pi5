import json
import pytest
from mono_record import Recorder,frame_record,MAX_FRAMES

def rec(seq=1):
    return frame_record(seq,12.3456789,7,True,[(7,'person',100.04,50.0,200.0,400.0,.91234,False,None),(8,'chair',1,2,3,4,.5,True)],
        dict(status='ok',reason='ok',z_m=1.2,roi=[1,2,3,4],coverage=.6,support=.9,geometry={'fx':1}),{7:[0.123456,0.5]},{7:[0.25,0.75]})

def test_record_holds_numbers_only_and_is_compact():
    r=rec();assert r['lock']==7 and r['pinned'] is True and r['t']==12.345679
    assert r['tracks'][0]=={'id':7,'label':'person','box':[100.0,50.0,200.0,400.0],'conf':.912,'coasting':False} and r['tracks'][1]['coasting'] is True
    assert r['stereo']=={'status':'ok','reason':'ok','z_m':1.2,'roi':[1,2,3,4],'coverage':.6,'support':.9}      # no geometry blob
    assert r['emb']=={'7':[0.1235,0.5]} and r['hist']=={'7':[0.25,0.75]}
    assert frame_record(1,1.0,None,False,[],None,None,None)['stereo'] is None
    json.dumps(r,allow_nan=False)

def test_recording_is_bounded_and_refuses_bad_requests(tmp_path):
    r=Recorder(tmp_path/'records')
    for label,seconds in [('Bad Label',10),('',10),('ok',0),('ok',301),('ok','10'),('../x',5),(None,5)]:
        with pytest.raises(ValueError):r.start(label,seconds,now=0.)
    path=r.start('near-far',2,now=100.)
    assert path.endswith('-near-far.jsonl') and r.active(100.5)
    with pytest.raises(ValueError):r.start('again',2,now=100.5)
    assert r.write(rec(1),now=100.5) and r.write(rec(2),now=101.9)
    assert not r.write(rec(3),now=102.0) and r.file is None                 # time limit: closed by itself
    lines=open(path).read().splitlines();assert [json.loads(l)['seq'] for l in lines]==[1,2]
    r.start('second',5,now=200.);r.frames=MAX_FRAMES;assert not r.write(rec(),now=200.1)   # frame limit too
