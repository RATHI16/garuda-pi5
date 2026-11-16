import copy
import io
import json
import math
import pytest
from live_onboard import LiveGate,SenderStatus,validate


def snapshot(seq=1,generation=0,request='off',camera='cm3'):
    return dict(kind='pi_onboard',age_ms=50,transmit=dict(request=request,generation=generation,instance='pi-1'),
        stats=dict(instance='pi-1',capabilities=dict(mount='vehicle',camera=camera,distance_source='stereo' if camera=='oak' else 'mono')),
        frame=dict(seq=seq,epoch='camera-1'),lock=dict(id=7,coasting=False),
        tracks=[dict(id=7,label='person',conf=.9,coasting=False)],
        onboard=dict(valid=True,transmit=False,frame_seq=seq,frame_epoch='camera-1',instance='pi-1',
                     target_id=7,forward_vel=.3,yaw_rate=.2,vertical_vel=0.,
                     range_source='stereo' if camera=='oak' else 'box_height',range_m=3. if camera=='oak' else None))


def active():
    g=LiveGate();assert g.evaluate(snapshot(),50,50,True) is None
    assert g.evaluate(snapshot(2,1,'sim'),50,50,True) is not None
    return g


def test_startup_requires_new_request_not_preexisting_switch():
    g=LiveGate();s=snapshot(1,7,'sim')
    assert g.evaluate(s,50,50,True) is None
    assert g.evaluate(s,50,50,True) is None
    assert g.evaluate(snapshot(2,8,'off'),50,50,True) is None
    assert g.evaluate(snapshot(3,9,'sim'),50,50,True) is not None
    assert not g.was_sending
    assert g.report()['state']=='ready'
    assert g.report(True)['state']=='sending'


@pytest.mark.parametrize('change',[
    lambda s:s['onboard'].update(target_id=8),lambda s:s['onboard'].update(frame_seq=1),
    lambda s:s['onboard'].update(instance='other'),lambda s:s['onboard'].update(valid=False),
    lambda s:s['onboard'].update(transmit=True),lambda s:s['onboard'].update(vertical_vel=.1),
    lambda s:s['onboard'].update(forward_vel=.81),lambda s:s['onboard'].update(forward_vel=-.41),
    lambda s:s['onboard'].update(yaw_rate=.51),lambda s:s['onboard'].update(forward_vel=math.nan),
    lambda s:s['onboard'].update(yaw_rate=True),lambda s:s['lock'].update(coasting=True),
    lambda s:s['tracks'][0].update(label='chair'),lambda s:s['tracks'][0].update(conf=.49),
    lambda s:s['stats']['capabilities'].update(mount='fixed'),lambda s:s.update(error='outage'),
    lambda s:s['stats'].update(identity={'confirmed':False}),
    lambda s:s['onboard'].update(range_source='stereo',range_m=3),
])
def test_bad_command_latches_and_valid_packet_does_not_resume(change):
    g=active();s=snapshot(3,1,'sim');change(s)
    assert g.evaluate(s,50,50,True) is None
    assert g.evaluate(snapshot(4,1,'sim'),50,50,True) is None
    assert g.evaluate(snapshot(5,2,'sim'),50,50,True) is not None


@pytest.mark.parametrize('age,progress',[(401,20),(20,401),(math.nan,20),(-1,20),(True,20)])
def test_freshness_or_progress_failure_needs_new_generation(age,progress):
    g=active();assert g.evaluate(snapshot(3,1,'sim'),age,progress,True) is None
    assert g.evaluate(snapshot(4,1,'sim'),20,20,True) is None


def test_restart_epoch_sequence_and_explicit_hold():
    g=active();g.drop('lease expired');assert g.evaluate(snapshot(3,1,'sim'),50,50,True) is None
    assert g.evaluate(snapshot(4,2,'sim'),50,50,True)
    assert g.evaluate(snapshot(3,2,'sim'),50,50,True) is None
    s=snapshot(5,3,'sim');s['stats']['instance']='new';s['onboard']['instance']='new';s['transmit']['instance']='new'
    assert g.evaluate(s,50,50,True) is None
    s['transmit']['generation']=4;assert g.evaluate(s,50,50,True)
    s['frame']['epoch']='new-camera';s['onboard']['frame_epoch']='new-camera'
    assert g.evaluate(s,50,50,True) is None


def test_stereo_yaw_only_when_range_absent_never_mono_fallback():
    s=snapshot(camera='oak');s['onboard'].update(range_m=None,forward_vel=0)
    assert validate(s,20,20)[0]
    s['onboard']['forward_vel']=.1;assert validate(s,20,20)[0] is None
    s['onboard'].update(range_source='box_height',range_m=None)
    assert validate(s,20,20)[0] is None


def test_status_network_is_outside_tick_and_binding_is_checked():
    calls=[]
    def open(req,timeout):
        calls.append((req.full_url,timeout));return io.BytesIO(json.dumps(dict(ok=True,instance='pi',generation=1,request='sim')).encode())
    status=SenderStatus(open);status.publish(dict(state='sending',reason='test',generation=1,instance='pi'))
    assert not calls
    status.send('http://pi');assert len(calls)==1 and status.take_error() is None
    status.send('http://pi');assert len(calls)==1
    status.publish(dict(state='sending',reason='test',generation=2,instance='pi'));status.last=-math.inf
    status.send('http://pi');assert 'changed' in status.take_error()
    status.last=-math.inf;status.published_at-=1;status.send('http://pi')
    assert 'state=holding' in calls[-1][0]


def test_real_pi_status_contract_registers_holds_and_sends(monkeypatch):
    from pathlib import Path
    from urllib.parse import parse_qs,urlparse
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]/'hosts/pi5'))
    from mono_transmit import Transmit
    pi=Transmit('pi-1');g=LiveGate()
    def open(req,timeout):
        q={k:v[0] for k,v in parse_qs(urlparse(req.full_url).query).items()}
        reply=pi.report(q['target'],q['state'],q.get('reason',''),1.,
                        int(q['generation']) if 'generation' in q else None,q.get('instance'))
        return io.BytesIO(json.dumps(dict(reply,ok=True)).encode())
    status=SenderStatus(open)
    def report():
        status.publish(g.report());status.last=-math.inf;status.send('http://pi')
        assert status.take_error() is None
    report()  # startup READY with no frame/request yet must register
    s=snapshot();s['transmit']=pi.snapshot(1.);g.evaluate(s,20,20,True);report()
    pi.ask('sim','vehicle',1.)
    s=snapshot(2);s['transmit']=pi.snapshot(1.);assert g.evaluate(s,20,20,True)
    g.report(True);report()
    assert pi.snapshot(1.)['listeners']['sim']['state']=='sending'
    g.drop('test outage');report()
    assert pi.snapshot(1.)['listeners']['sim']['state']=='holding'
    pi.ask('off','vehicle',1.);s=snapshot(3);s['transmit']=pi.snapshot(1.)
    assert g.evaluate(s,20,20,True) is None
    report()  # a bound HOLD for request=off is not a transport failure


@pytest.mark.parametrize('instance',[None,'old-detector'])
def test_request_from_missing_or_different_detector_cannot_grant(instance):
    g=active();s=snapshot(3,2,'sim');s['transmit']['instance']=instance
    assert g.evaluate(s,20,20,True) is None
    assert g.grant is None


def test_late_feedback_error_cannot_invalidate_a_new_request():
    def fail(*a,**k):raise TimeoutError('old request')
    status=SenderStatus(fail)
    status.publish(dict(state='sending',generation=1,instance='pi',reason='old'))
    status.send('http://pi')
    assert status.take_error(('pi',2)) is None
    status.last=-math.inf;status.send('http://pi')
    assert 'old request' in status.take_error(('pi',1))
