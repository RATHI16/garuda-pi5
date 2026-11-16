"""Exact-ID selection check used by the detector's /api/select_track route."""
import pytest
from mono_state import select_by_id

def state():
    return {"t":100.0,"frame_seq":41,"frame_epoch":"oak-rgb:abc:0","tracks_full":[
        (7,"person",100,50,200,400,.9,False),(8,"suitcase",150,300,60,80,.7,False),(9,"chair",0,0,10,10,.6,True)]}
def q(**k):return {name:[str(v)] for name,v in k.items()}
GOOD=dict(id=8,epoch="oak-rgb:abc:0",instance="proc");NOW=100.2

def test_selects_exactly_the_requested_id_even_inside_a_larger_box():
    # The suitcase box lies inside the person box; the coordinate route would need a point.
    ok,body=select_by_id(state(),"proc",q(**GOOD),NOW)
    assert ok and body=={"ok":True,"id":8,"label":"suitcase","epoch":"oak-rgb:abc:0","instance":"proc","seq":41}
    ok,body=select_by_id(state(),"proc",q(**dict(GOOD,id=7)),NOW)
    assert ok and body["label"]=="person"

@pytest.mark.parametrize("change,reason",[
    (dict(instance="other"),"detector_restarted"),(dict(epoch="oak-rgb:abc:1"),"frame_epoch_changed"),
    (dict(id=99),"track_not_present"),(dict(id=9),"track_coasting")])
def test_refusals_name_the_cause(change,reason):
    assert select_by_id(state(),"proc",q(**dict(GOOD,**change)),NOW)==(False,{"ok":False,"reason":reason})

@pytest.mark.parametrize("query",[{},q(id=8),q(id=8,epoch="oak-rgb:abc:0"),q(**dict(GOOD,id="-1")),q(**dict(GOOD,id="8.0")),
    q(**dict(GOOD,id="9"*13)),{**q(**GOOD),"id":["7","8"]},{**q(**GOOD),"x":["1"]}])
def test_malformed_requests_select_nothing(query):
    ok,body=select_by_id(state(),"proc",query,NOW)
    assert not ok and body["ok"] is False

def test_no_processed_frame_selects_nothing():
    assert select_by_id({"tracks_full":[]},"proc",q(**GOOD),NOW)[1]["reason"]=="frame_epoch_changed"


def test_a_stale_published_track_cannot_be_selected():
    # The published state keeps its last tracks when capture stops (astra-0058 finding 2).
    assert select_by_id(state(),"proc",q(**GOOD),100.69)[0] is True         # just inside the bound
    assert select_by_id(state(),"proc",q(**GOOD),100.71)==(False,{"ok":False,"reason":"observation_stale"})
    assert select_by_id(state(),"proc",q(**GOOD),5000.0)[1]["reason"]=="observation_stale"
    assert select_by_id(state(),"proc",q(**GOOD),101.0,max_age_s=2.0)[0] is True

@pytest.mark.parametrize("t,now",[(None,100.2),(0.0,100.2),(-1.0,100.2),(float("nan"),100.2),(float("inf"),100.2),("100",100.2),(True,100.2),
    (100.0,99.9),(100.0,float("nan")),(100.0,None)])
def test_missing_or_invalid_observation_time_selects_nothing(t,now):
    s=state();s["t"]=t
    if t is None:del s["t"]
    assert select_by_id(s,"proc",q(**GOOD),now)==(False,{"ok":False,"reason":"observation_time_invalid"})


class _T:
    def __init__(self,tid,since=0):self.track_id=tid;self.time_since_update=since

def test_prune_unmatched_bounds_reattachment_by_capture_time():
    from mono_state import prune_unmatched,note_matched
    seen={};a,b,new=_T(1),_T(2,since=3),_T(3)
    note_matched([a,b],seen,10.0);assert seen=={1:10.0}                 # b was not matched on this frame
    seen[2]=9.0
    kept=prune_unmatched([a,b,new],seen,10.4,0.5);assert [t.track_id for t in kept]==[1,3] and seen=={1:10.0}
    assert [t.track_id for t in prune_unmatched([a],seen,10.5,0.5)]==[1]    # exactly at the bound is kept
    assert prune_unmatched([a],dict(seen),10.51,0.5)==[]
    for bad in (9.0,float('nan'),float('inf'),None,True):               # backwards or unusable clock: nothing is trusted
        assert prune_unmatched([a],{1:10.0},bad,0.5)==[]
    assert [t.track_id for t in prune_unmatched([new],{},float('nan'),0.5)]==[3]   # a brand-new track has no history to distrust
