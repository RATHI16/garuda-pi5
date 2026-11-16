import pytest
from control_lease import ControlLease


def command(lease, session='tab-a', now=0, name='follow'):
    return lease.submit({'command': name}, session, now)


def test_only_owning_heartbeat_renews_and_gets_do_not():
    c=ControlLease();command(c)
    with pytest.raises(ValueError):c.heartbeat('tab-b',1)
    for n in (1,2,3):c.state(n)
    assert c.consume_disconnect(3) and not c.consume_disconnect(3)
    assert c.state(3)['expired'] and not c.gestures_allowed(3)
    with pytest.raises(ValueError):c.heartbeat('tab-a',3)
    command(c,now=3.1)
    c.heartbeat('tab-a',5)
    assert not c.consume_disconnect(7) and c.state(7)['owner']=='tab-a'


def test_other_tab_cannot_take_over_but_can_hold_or_land():
    c=ControlLease();command(c)
    with pytest.raises(ValueError):command(c,'tab-b',1)
    for name in ('hold','land'):
        ticket=command(c,'tab-b',1,name)
        calls=[]
        assert c.execute(ticket,1,lambda a,t:calls.append(a)) is None
        assert calls==[{'command':name}] and c.state(1)['owner']=='tab-a'
    assert c.consume_disconnect(3)  # emergency commands do not renew someone else's lease


def test_queued_command_cannot_outlive_lease_or_new_owner():
    c=ControlLease();old=command(c)
    command(c,'tab-b',4)
    called=[]
    assert c.execute(old,4,lambda *a:called.append(a))
    assert not called and c.state(4)['last_command']['applied'] is False
    assert c.consume_disconnect(4)  # reacquiring must not erase pending hold


def test_applied_ack_is_only_written_after_successful_callback():
    c=ControlLease();t=command(c)
    assert c.state(0)['last_command'] is None
    assert c.execute(t,0,lambda a,n:None) is None
    assert c.state(0)['last_command']==dict(id=t['id'],name='follow',applied=True,error=None)
    def fail(*a):raise ValueError('Telemetry stale')
    assert c.execute(command(c,now=1),1,fail)=='Telemetry stale'
    assert not c.state(1)['last_command']['applied']


def test_legacy_headless_cannot_bypass_browser_ownership_after_expiry():
    c=ControlLease();command(c,None)
    command(c)
    with pytest.raises(ValueError):command(c,None,4)
    t=command(c,None,4,'hold')
    assert c.execute(t,4,lambda *a:None) is None


@pytest.mark.parametrize('session',['', 'a b', 'x'*129, 5, '\n'])
def test_bad_sessions_rejected(session):
    with pytest.raises(ValueError):command(ControlLease(),session)
