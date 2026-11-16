"""Ground relay audit tests: no network or vehicle touched."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from fc_live_ground import audit_outgoing


def messages(vx=0.,yaw=0.,system=4,component=191,target=4):
    ref=pytest.importorskip('pymavlink.dialects.v20.common')
    m=ref.MAVLink(None,srcSystem=system,srcComponent=component)
    packet=m.set_position_target_local_ned_encode(100,target,1,8,1479,0,0,0,vx,0,0,0,0,0,0,yaw).pack(m)
    return ref.MAVLink(None).parse_buffer(packet)


def test_only_zero_setpoints_to_disarmed_pinned_simulator_are_forwardable():
    assert audit_outgoing(messages(),(10.,dict(base_mode=0)),10.1)==1
    for packet in [messages(vx=.01),messages(yaw=.01),messages(system=1),messages(component=190),messages(target=1),[]]:
        with pytest.raises(RuntimeError):audit_outgoing(packet,(10.,dict(base_mode=0)),10.1)


def test_unknown_stale_or_armed_fc_cannot_receive_even_zeros():
    for hb in [None,(8.,dict(base_mode=0)),(10.,dict(base_mode=128))]:
        with pytest.raises(RuntimeError):audit_outgoing(messages(),hb,10.1)


def test_command_long_cannot_pass_the_relay():
    ref=pytest.importorskip('pymavlink.dialects.v20.common')
    m=ref.MAVLink(None,srcSystem=4,srcComponent=191)
    packet=m.command_long_encode(4,1,400,0,1,0,0,0,0,0,0).pack(m)
    with pytest.raises(RuntimeError,match='forbidden'):audit_outgoing(ref.MAVLink(None).parse_buffer(packet),(10.,dict(base_mode=0)),10.1)


def test_default_only_prints_plan():
    p=subprocess.run([sys.executable,str(Path(__file__).with_name('fc_live_ground.py'))],capture_output=True,text=True,check=True)
    assert json.loads(p.stdout)['sends'] is False
