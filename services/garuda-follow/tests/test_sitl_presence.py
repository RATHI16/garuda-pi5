import pytest
import mavlink_min as mav
from sitl_presence import SitlPresence

class Socket:
    def __init__(self): self.packets=[]; self.closed=False
    def bind(self, at): self.bound=at
    def setblocking(self, value): self.blocking=value
    def sendto(self, data, at): self.packets.append((data,at))
    def close(self): self.closed=True

def test_presence_is_simulator_only_and_no_commands(monkeypatch):
    monkeypatch.setenv('GARUDA_SIM','1');monkeypatch.setenv('ROS_DOMAIN_ID','42')
    s=Socket();p=SitlPresence(s)
    assert s.bound==('127.0.0.1',14543)
    p.tick(0,True);p.tick(.5,True);p.tick(1,True);p.tick(2,False);p.tick(3,False)
    assert len(s.packets)==2
    for packet,at in s.packets:
        assert at==('127.0.0.1',14583)
        messages=mav.parse(packet)
        assert len(messages)==1 and messages[0]['kind']=='heartbeat'
        assert messages[0]['sysid']==4 and messages[0]['compid']==191
        assert messages[0]['type']==18
    p.close();assert s.closed

@pytest.mark.parametrize('sim,domain',[('0','42'),('1','0'),('','')])
def test_presence_refuses_other_environments(monkeypatch,sim,domain):
    monkeypatch.setenv('GARUDA_SIM',sim);monkeypatch.setenv('ROS_DOMAIN_ID',domain)
    with pytest.raises(ValueError):SitlPresence(Socket())
