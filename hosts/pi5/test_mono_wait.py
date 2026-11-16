from mono_wait import parse_after

def test_parse_after():
    assert parse_after({}) == (True, None)
    assert parse_after({"after": ["12"]}) == (True, 12)
    assert parse_after({"after": ["-1"]}) == (True, -1)
    for bad in [{"after": ["x"]}, {"after": ["1", "2"]}, {"after": ["-2"]}, {"after": ["1"], "b": ["2"]}, {"b": ["2"]}, {"after": [""]}]:
        assert parse_after(bad)[0] is False


def test_event_stream_cursor_is_the_snapshot_that_was_written():
    """astra-0058 finding 4: a result published while snapshot N is being written must be
    recognised at once, not after the heartbeat or the result after it."""
    import threading,time
    from mono_wait import event_stream,frame_identity
    cond=threading.Condition();state={"epoch":"E","seq":1};written=[];waits=[]
    def snapshot():return {"frame":dict(state)}
    def write(snap):
        written.append(snap["frame"]["seq"])
        if snap["frame"]["seq"]==1:
            with cond:state["seq"]=2;cond.notify_all()      # N+1 is published during the write of N
    def wait_changed(sent,timeout):
        t0=time.monotonic()
        with cond:cond.wait_for(lambda:(state["epoch"],state["seq"])!=sent,timeout=timeout)
        waits.append(time.monotonic()-t0)
    event_stream(snapshot,write,wait_changed,heartbeat_s=2.0,rounds=2)
    assert written==[1,2] and waits[0]<0.5          # no heartbeat wait before N+1 went out
    assert waits[1]>=1.9                            # nothing newer: waits for the heartbeat
    assert frame_identity({})==(None,None) and frame_identity({"frame":{"epoch":"E","seq":3}})==("E",3)

def test_event_stream_sees_an_epoch_change_with_the_same_sequence_number():
    from mono_wait import event_stream
    state={"epoch":"A","seq":5};seen=[]
    def write(snap):state["epoch"]="B"
    def wait_changed(sent,timeout):seen.append(((state["epoch"],state["seq"])!=sent))
    event_stream(lambda:{"frame":dict(state)},write,wait_changed,rounds=1)
    assert seen==[True]
