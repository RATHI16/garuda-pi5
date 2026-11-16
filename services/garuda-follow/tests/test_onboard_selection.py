import io
import json
from onboard_selection import OnboardSelection,current_command
from sitl_demo_core import DemoControl
from .test_onboard_source import snap


def test_release_boundary_and_same_id_reselection_are_new_generations():
    c=DemoControl();c.accept_onboard=True;c.receive(snap(),0);c.select(7)
    sync=OnboardSelection();sent=[]
    def opener(request,timeout):sent.append(json.loads(request.data));return io.BytesIO(b'{"ok":true}')
    source=dict(kind='onboard',generation=1,url='http://127.0.0.1:8101')
    sync.publish(c,True);sync.sync(source,opener)
    first=sent[-1]['selection_gen'];assert sent[-1]['onboard_select']==7
    assert current_command(c) is not None
    c.reset();sync.publish(c,True)
    assert current_command(c) is None
    sync.sync(source,opener);assert sent[-1]['onboard_select'] is None
    c.receive(snap(),.1);c.select(7);sync.publish(c,True);sync.sync(source,opener)
    assert sent[-1]['onboard_select']==7 and sent[-1]['selection_gen']>first
    assert current_command(c) is None  # old target ID is same, but old command cannot resume
    c.boundary('test outage');sync.publish(c,True);sync.sync(source,opener)
    assert sent[-1]['onboard_select'] is None


def test_new_intent_during_network_wait_is_sent_on_next_iteration():
    c=DemoControl();c.receive(snap(),0);c.select(7);sync=OnboardSelection();sync.publish(c,True)
    source=dict(kind='onboard',generation=1,url='http://127.0.0.1:8101');sent=[]
    def opener(request,timeout):
        sent.append(json.loads(request.data));c.reset();sync.publish(c,True)
        return io.BytesIO(b'{"ok":true}')
    sync.sync(source,opener)
    sync.sync(source,opener)
    assert sent[0]['onboard_select']==7 and sent[1]['onboard_select'] is None


def test_refusal_is_reported_without_starving_observations_or_retrying_same_intent():
    from urllib.error import HTTPError
    from onboard_selection import read_with_selection
    from types import SimpleNamespace
    c=DemoControl();c.receive(snap(),0);c.select(7)
    calls=[]
    def opener(req,timeout):
        data=json.loads(req.data);calls.append(data)
        if len(calls)==1:
            raise HTTPError(req.full_url,400,'Bad selection',{},io.BytesIO(b'{"error":"Selected person unavailable"}'))
        return io.BytesIO(b'{"ok":true}')
    sync=OnboardSelection(opener);sync.publish(c,True)
    source=dict(kind='onboard',generation=1,url='http://127.0.0.1:8101')
    read=[];reader=SimpleNamespace(read=lambda source:read.append(True) or {'new':'observation'})
    assert read_with_selection(reader,sync,source)=={'new':'observation'}
    assert read_with_selection(reader,sync,source)=={'new':'observation'}
    assert len(read)==2 and len(calls)==1  # refusal remembered; data still arrives
    assert 'Selected person unavailable' in sync.consume_refusal(source,c)
    c.track(.1)
    assert c.requires_reselection and 'Onboard selection refused' in c.reason
    sync.publish(c,True);read_with_selection(reader,sync,source)
    assert calls[-1]['onboard_select'] is None
    c.receive(snap(2),.2);c.select(7);sync.publish(c,True);read_with_selection(reader,sync,source)
    assert calls[-1]['onboard_select']==7 and sync.state()['error'] is None


def test_transport_error_retries_but_reads_and_new_selection_ignore_old_refusal():
    from onboard_selection import read_with_selection
    from types import SimpleNamespace
    c=DemoControl();c.receive(snap(),0);c.select(7);calls=[]
    def opener(req,timeout):calls.append(req);raise TimeoutError('slow')
    sync=OnboardSelection(opener);sync.publish(c,True)
    source=dict(kind='onboard',generation=1,url='http://127.0.0.1:8101')
    reader=SimpleNamespace(read=lambda s:{'fresh':True})
    for _ in range(2):assert read_with_selection(reader,sync,source)=={'fresh':True}
    assert len(calls)==2 and sync.consume_refusal(source,c) is None
    old=c.selection_generation;c.select(7)
    sync.failure=dict(source_generation=1,selection_gen=old,target=7,reason='old refusal')
    assert sync.consume_refusal(source,c) is None and not c.requires_reselection
