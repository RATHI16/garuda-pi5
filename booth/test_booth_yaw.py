"""Offline tests for booth_yaw_bridge.BoothYaw v2 (heading setpoint). No network, no MAVLink.
Run: python3 -m pytest -q test_booth_yaw.py"""
import math
import types

import pytest

from booth_yaw_bridge import BoothYaw, endpoint_kind, wrap, RATE_HZ, TYPE_MASK_POS_YAW

DT = 1.0 / RATE_HZ
D = math.degrees


def fc(yaw_deg, offboard=True, x=1.0, y=2.0, z=-1.2):
    return dict(offboard=offboard, armed=True, x=x, y=y, z=z, yaw=math.radians(yaw_deg))


class Vehicle:
    """Heading follows the setpoint like a real multicopter: proportional, rate-limited, with lag.
    disturb_dps adds a constant external yaw push (deg/s) - what PX4 must reject."""
    def __init__(self, yaw_deg=0.0, k=3.0, max_dps=40.0, disturb_dps=0.0, follows=True):
        self.yaw, self.k, self.max, self.disturb, self.follows = yaw_deg, k, max_dps, disturb_dps, follows

    def update(self, sp_abs_rad):
        if self.follows:
            err = D(wrap(sp_abs_rad - math.radians(self.yaw)))
            self.yaw += max(-self.max, min(self.max, self.k * err)) * DT
        self.yaw += self.disturb * DT


def run(ctl, veh, seconds, t, cmd_rate, offboard=True):
    out = None
    for _ in range(int(round(seconds * RATE_HZ))):
        out = ctl.step(t, fc(veh.yaw, offboard), None if cmd_rate is None else {"yaw_rate": cmd_rate})
        if out["send"]:
            veh.update(out["yaw"])
        t += DT
    return t, out


def test_mask_is_position_plus_yaw_angle():
    # ignore vx,vy,vz (8,16,32), ax,ay,az (64,128,256), yaw_rate (2048); use x,y,z and yaw
    assert TYPE_MASK_POS_YAW == 8 + 16 + 32 + 64 + 128 + 256 + 2048
    assert not TYPE_MASK_POS_YAW & (1 | 2 | 4 | 1024)


def test_wrap():
    assert wrap(math.radians(190)) == pytest.approx(math.radians(-170))
    assert wrap(math.radians(-190)) == pytest.approx(math.radians(170))


def test_no_fc_sends_nothing():
    out = BoothYaw().step(0.0, None, {"yaw_rate": 0.3})
    assert out["send"] is False and out["state"] == "WAIT_FC"


def test_prestream_sends_current_position_and_heading():
    out = BoothYaw().step(0.0, fc(30, offboard=False, x=5, y=6, z=-1), {"yaw_rate": 0.5})
    assert out["send"] and out["state"] == "PRESTREAM"
    assert (out["x"], out["y"], out["z"]) == (5, 6, -1) and out["yaw"] == pytest.approx(math.radians(30))


def test_home_locked_on_entering_offboard_position_never_moves():
    ctl = BoothYaw()
    out = ctl.step(0.0, fc(10, x=1, y=2, z=-1.2), {"yaw_rate": 0.2})
    assert out["state"] == "TRACK" and (out["x"], out["y"], out["z"]) == (1, 2, -1.2)
    assert out["yaw"] == pytest.approx(math.radians(10))          # first tick: hold the entry heading
    out = ctl.step(DT, fc(12, x=3, y=4, z=-0.5), {"yaw_rate": 0.2})
    assert (out["x"], out["y"], out["z"]) == (1, 2, -1.2)


def test_setpoint_moves_at_most_max_rate():
    ctl = BoothYaw(max_yaw_rate=0.3)
    ctl.step(0.0, fc(0), {"yaw_rate": 0.0})
    out = ctl.step(DT, fc(0), {"yaw_rate": 0.5})
    assert out["sp_rel_deg"] == pytest.approx(D(0.3 * DT))


def test_zero_command_holds_heading_against_disturbance():
    """v1 bug from the flight logs: heading drifted 20+ deg while the command was 0."""
    ctl, veh = BoothYaw(), Vehicle(disturb_dps=4.0)
    t, out = run(ctl, veh, 10.0, 0.0, 0.0)
    assert out["sp_rel_deg"] == pytest.approx(0.0)                # the target stays at home
    assert abs(veh.yaw) < 3.0                                     # vehicle model is pulled back to it


def test_setpoint_never_exceeds_limit_and_vehicle_barely_does():
    ctl, veh = BoothYaw(yaw_limit_deg=30, max_yaw_rate=0.3, beyond_s=1.5), Vehicle()
    worst_sp, worst_yaw, t = 0.0, 0.0, 0.0
    for _ in range(int(6 * RATE_HZ)):                             # person keeps pulling right
        out = ctl.step(t, fc(veh.yaw), {"yaw_rate": 0.3})
        veh.update(out["yaw"]); t += DT
        worst_sp, worst_yaw = max(worst_sp, out["sp_rel_deg"]), max(worst_yaw, veh.yaw)
    assert worst_sp <= 30.0 + 1e-6
    assert worst_yaw <= 30.5
    assert out["state"] in ("RETURN", "CENTRED")


def test_after_return_it_does_not_chase_again():
    ctl, veh = BoothYaw(yaw_limit_deg=30, beyond_s=1.5, return_rate=0.25), Vehicle()
    t, _ = run(ctl, veh, 6.0, 0.0, 0.3)
    t, out = run(ctl, veh, 8.0, t, 0.3)                           # keeps pulling hard
    assert out["state"] == "CENTRED" and abs(out["sp_rel_deg"]) < 1e-6 and abs(veh.yaw) <= 3.0


def test_lost_person_returns_to_centre():
    ctl, veh = BoothYaw(lost_s=2.0), Vehicle()
    t, _ = run(ctl, veh, 2.0, 0.0, -0.3)
    assert veh.yaw < -20
    t, out = run(ctl, veh, 1.5, t, None)                          # lost < lost_s: holds heading
    assert out["state"] == "TRACK" and out["reason"].startswith("no valid command")
    held = out["sp_rel_deg"]
    t, out = run(ctl, veh, 1.0, t, None)
    assert out["state"] == "RETURN" and out["sp_rel_deg"] > held  # moving back toward 0
    t, out = run(ctl, veh, 10.0, t, None)
    assert out["state"] == "CENTRED" and abs(veh.yaw) <= 3.0


def test_turning_back_toward_centre_is_allowed_at_the_limit():
    """v1 bug: past the limit, the overshoot clamp also blocked turning back."""
    ctl, veh = BoothYaw(yaw_limit_deg=30), Vehicle()
    t, _ = run(ctl, veh, 4.0, 0.0, 0.3)                           # reach +30
    t, out = run(ctl, veh, 0.5, t, -0.2)                          # person now left of centre
    assert out["sp_rel_deg"] < 30.0 - 4.0


def test_vehicle_pushed_beyond_limit_returns():
    ctl = BoothYaw(yaw_limit_deg=30, beyond_s=1.5)
    t = 0.0
    ctl.step(t, fc(0), {"yaw_rate": 0.0})
    for _ in range(int(2 * RATE_HZ)):                             # something holds it at +40 deg
        t += DT
        out = ctl.step(t, fc(40), {"yaw_rate": 0.0})
    assert out["state"] == "RETURN"


def test_setpoint_does_not_run_away_from_a_vehicle_that_does_not_turn():
    ctl, veh = BoothYaw(yaw_limit_deg=45, max_lead_deg=15), Vehicle(follows=False)   # e.g. disarmed
    t, out = run(ctl, veh, 5.0, 0.0, 0.3)
    assert out["sp_rel_deg"] <= 15.0 + 1e-6


def test_lead_limit_never_drags_the_setpoint():
    ctl = BoothYaw(max_lead_deg=15)
    ctl.step(0.0, fc(0), {"yaw_rate": 0.0})
    out = ctl.step(DT, fc(-25), {"yaw_rate": 0.0})                # pushed far off by a gust
    assert out["sp_rel_deg"] == pytest.approx(0.0)                # target stays home


def test_resume_only_near_centre_after_return():
    ctl = BoothYaw(resume_rate=0.25)
    ctl.step(0.0, fc(0), {"yaw_rate": 0.0})
    ctl.state = "CENTRED"
    out = ctl.step(DT, fc(0), {"yaw_rate": 0.4})
    assert out["sp_rel_deg"] == pytest.approx(0.0) and out["state"] == "CENTRED"
    out = ctl.step(2 * DT, fc(0), {"yaw_rate": 0.1})
    assert out["state"] == "TRACK" and out["sp_rel_deg"] == pytest.approx(D(0.1 * DT))


def test_leaving_offboard_clears_home():
    ctl = BoothYaw()
    ctl.step(0.0, fc(0, x=1), {"yaw_rate": 0.1})
    ctl.step(DT, fc(5, offboard=False, x=9), None)
    out = ctl.step(2 * DT, fc(5, x=9), None)
    assert out["x"] == 9 and ctl.home[3] == pytest.approx(math.radians(5)) and out["sp_rel_deg"] == 0.0


def test_heading_wraps_across_180():
    ctl = BoothYaw(yaw_limit_deg=45)
    ctl.step(0.0, fc(170), {"yaw_rate": 0.0})                    # home at 170 deg
    out = ctl.step(DT, fc(-170), {"yaw_rate": 0.1})              # +20 deg past home, across the wrap
    assert out["yaw_rel_deg"] == pytest.approx(20.0, abs=0.01)
    assert -math.pi < out["yaw"] <= math.pi


@pytest.mark.parametrize("env,flags,ok", [
    ({}, ["--simulator"], False),
    ({"GARUDA_SIM": "1"}, ["--simulator"], True),
    ({"GARUDA_FC_BRIDGE": "1"}, ["--real-flight-controller"], True),
    ({"GARUDA_SIM": "1", "GARUDA_FC_BRIDGE": "1"}, ["--simulator"], False),
])
def test_endpoint_gate(env, flags, ok):
    sim = "--simulator" in flags
    a = types.SimpleNamespace(simulator=sim, real_flight_controller=not sim,
                              listen="127.0.0.1:14543" if sim else "0.0.0.0:14540",
                              fc_host="127.0.0.1" if sim else "192.168.100.3")
    if ok:
        assert endpoint_kind(a, env) in ("simulator", "flight controller")
    else:
        with pytest.raises(SystemExit):
            endpoint_kind(a, env)


# ---- no yaw limit (--no-yaw-limit) ----

def test_no_limit_follows_past_90_and_past_180():
    ctl, veh = BoothYaw(yaw_limit_deg=None, max_yaw_rate=0.3), Vehicle()
    t, out = run(ctl, veh, 15.0, 0.0, 0.3)                       # 0.3 rad/s * 15 s = 258 deg
    assert out["state"] == "TRACK" and out["sp_rel_deg"] > 240
    assert out["yaw_rel_deg"] > 230                               # unwrapped: real heading followed round
    assert -math.pi < out["yaw"] <= math.pi                       # what is sent is always wrapped


def test_no_limit_lost_person_holds_heading_no_return():
    ctl, veh = BoothYaw(yaw_limit_deg=None, lost_s=2.0), Vehicle()
    t, _ = run(ctl, veh, 3.0, 0.0, 0.3)
    t, out = run(ctl, veh, 10.0, t, None)
    held = out["sp_rel_deg"]
    assert out["state"] == "TRACK" and held > 40
    assert abs(veh.yaw - held) < 2.0                              # stays where it was, does not turn back


def test_no_limit_still_holds_heading_against_disturbance_and_lead_limit():
    ctl, veh = BoothYaw(yaw_limit_deg=None), Vehicle(disturb_dps=4.0)
    t, out = run(ctl, veh, 10.0, 0.0, 0.0)
    assert out["sp_rel_deg"] == pytest.approx(0.0) and abs(veh.yaw) < 3.0
    ctl, veh = BoothYaw(yaw_limit_deg=None, max_lead_deg=15), Vehicle(follows=False)
    t, out = run(ctl, veh, 5.0, 0.0, 0.3)
    assert out["sp_rel_deg"] <= 15.0 + 1e-6


# ---- guard rails (Bhanu's fc_gate rules) ----
from booth_yaw_bridge import FcState

H, A = "192.168.100.3", ("192.168.100.3", 14540)


def fed(st, now, boot, yaw=0.0, offboard=False, armed=False, addr=A, sysid=1):
    st.note("HEARTBEAT", sysid, 1, addr, now, px4=True, offboard=offboard, armed=armed)
    st.note("ATTITUDE", sysid, 1, addr, now, time_boot_ms=boot, yaw=yaw)
    st.note("LOCAL_POSITION_NED", sysid, 1, addr, now, x=0.0, y=0.0, z=-1.0)


def test_fcstate_fresh_only_with_advancing_attitude():
    st = FcState(H, 1)
    fed(st, 0.0, 1000)
    assert st.view(0.1) is not None
    st.note("ATTITUDE", 1, 1, A, 0.2, time_boot_ms=1000, yaw=0.0)    # same timestamp: not advancing
    assert st.view(0.35) is None                                      # older than 0.3 s


def test_fcstate_other_host_ignored_and_port_pinned():
    st = FcState(H, 1)
    fed(st, 0.0, 1000, addr=("192.168.100.99", 14540))
    assert st.view(0.0) is None and st.address is None
    fed(st, 0.0, 1000)
    fed(st, 0.1, 1100, addr=(H, 55555))                               # same host, other port
    assert st.view(0.15) is None and "port" in st.pop_event()


def test_fcstate_other_sysid_from_fc_host_is_conflict():
    st = FcState(H, 1)
    fed(st, 0.0, 1000)
    st.note("HEARTBEAT", 7, 1, A, 0.1, px4=True, offboard=False, armed=False)
    assert st.view(0.2) is None


def test_fcstate_reboot_detected():
    st = FcState(H, 1)
    fed(st, 0.0, 50000)
    fed(st, 0.1, 300)                                                  # boot time went backwards
    assert st.pop_event() == "flight controller rebooted" and st.view(0.1) is None


def test_fcstate_heartbeat_timeout():
    st = FcState(H, 1)
    fed(st, 0.0, 1000)
    for i in range(1, 40):                                             # attitude keeps coming, heartbeat stops
        st.note("ATTITUDE", 1, 1, A, i * 0.05, time_boot_ms=1000 + i * 50, yaw=0.0)
    assert st.view(1.95) is None


def test_fcstate_disarm_event_even_if_rearmed_in_same_read():
    st = FcState(H, 1)
    fed(st, 0.0, 1000, offboard=True, armed=True)
    st.note("HEARTBEAT", 1, 1, A, 0.05, px4=True, offboard=True, armed=False)
    st.note("HEARTBEAT", 1, 1, A, 0.05, px4=True, offboard=True, armed=True)
    assert st.pop_event() == "flight controller disarmed"


def test_link_loss_in_offboard_locks_out_until_position():
    ctl = BoothYaw(yaw_limit_deg=None)
    ctl.step(0.0, fc(0), {"yaw_rate": 0.1})                           # TRACK
    ctl.step(0.05, None, None)                                         # link lost
    out = ctl.step(0.10, fc(0), {"yaw_rate": 0.1})                     # link back, PX4 still in OFFBOARD
    assert out["state"] == "LOCKOUT" and out["send"] is False
    out = ctl.step(0.15, fc(0, offboard=False), None)                  # pilot -> Position
    assert out["state"] == "PRESTREAM" and out["send"] is True
    out = ctl.step(0.20, fc(0), {"yaw_rate": 0.1})                     # pilot -> OFFBOARD again
    assert out["state"] == "TRACK" and out["send"] is True


def test_disarm_in_offboard_locks_out():
    ctl = BoothYaw()
    ctl.step(0.0, dict(fc(0), armed=True), None)
    out = ctl.step(0.05, dict(fc(0), armed=False), None)
    assert out["state"] == "LOCKOUT" and out["send"] is False


def test_loop_stall_event_in_offboard_locks_out():
    ctl = BoothYaw()
    ctl.step(0.0, fc(0), None)
    ctl.step(0.30, None, None, "bridge loop stalled 300 ms")
    out = ctl.step(0.35, fc(0), None)
    assert out["state"] == "LOCKOUT" and "stalled" in out["reason"]


def test_link_loss_outside_offboard_does_not_lock_out():
    ctl = BoothYaw()
    ctl.step(0.0, fc(0, offboard=False), None)
    ctl.step(0.05, None, None)
    out = ctl.step(0.10, fc(0, offboard=False), None)
    assert out["state"] == "PRESTREAM" and out["send"]


def test_feed_forward_mask_uses_yaw_and_yaw_rate():
    from booth_yaw_bridge import TYPE_MASK_POS_YAW_FF
    assert TYPE_MASK_POS_YAW_FF == 8 + 16 + 32 + 64 + 128 + 256        # ignore velocity + acceleration only
    assert not TYPE_MASK_POS_YAW_FF & (1 | 2 | 4 | 1024 | 2048)


def test_feed_forward_equals_setpoint_rate_and_is_zero_when_holding():
    ctl = BoothYaw(yaw_limit_deg=None, max_yaw_rate=0.2)
    ctl.step(0.0, fc(0), {"yaw_rate": 0.0})
    out = ctl.step(DT, fc(0), {"yaw_rate": 0.15})
    assert out["yaw_rate"] == pytest.approx(0.15)
    out = ctl.step(2 * DT, fc(0), None)
    assert out["yaw_rate"] == 0.0
