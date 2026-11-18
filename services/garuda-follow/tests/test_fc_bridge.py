import io
import json
import struct

import pytest

import mavlink_min as mav
from fc_bridge import Bridge, PiState, endpoint_kind
from fc_gate import FcGate, FcLink
from .test_live_onboard import snapshot as live_snapshot

ADDR = ("10.0.0.5", 14580)
SIM_ADDR = ("127.0.0.1", 14580)


def snap(seq=1, generation=0, request="off", camera="cm3"):
    s = live_snapshot(seq, generation, "sim" if request != "off" else "off", camera)
    s["transmit"]["request"] = request
    return s


def px4(offboard=True, armed=True, sysid=1, compid=1, autopilot=mav.MAV_AUTOPILOT_PX4):
    mode = (mav.PX4_MAIN_MODE_OFFBOARD if offboard else 4) << 16
    payload = struct.pack("<IBBBBB", mode, 2, autopilot, 0x80 if armed else 0, 4, 3)
    return mav.frame(mav.HEARTBEAT, payload, 0, sysid, compid)


def attitude(boot_ms, sysid=1, compid=1):
    return mav.frame(mav.ATTITUDE, struct.pack("<I6f", boot_ms, .1, 0, 0, 0, 0, 0), 0, sysid, compid)


FC = {"sysid": 1, "offboard": True, "armed": True, "address": ADDR}
MANUAL = dict(FC, offboard=False)


def streaming(fc=FC):
    """A gate whose operator has opened a stream session (baseline, then fc_hold)."""
    g = FcGate()
    assert g.step(snap(), 50, 50, fc)["stream"] is False             # start-up baseline: nothing flows
    out = g.step(snap(2, 1, "fc_hold"), 50, 50, fc)
    assert out["stream"] is True and out["state"] == "holding" and (out["forward_vel"], out["yaw_rate"]) == (0, 0)
    return g


def granted():
    g = streaming()
    out = g.step(snap(3, 2, "fc"), 50, 50, FC)
    assert out["state"] == "sending" and (out["forward_vel"], out["yaw_rate"]) == (.3, .2)
    return g


# ---- message encoding ---------------------------------------------------------------------
def test_encoding_matches_the_reference_library():
    mavutil = pytest.importorskip("pymavlink.mavutil")
    from pymavlink.dialects.v20 import common as ref
    m = ref.MAVLink(None, srcSystem=1, srcComponent=191)
    m.seq = 7
    want = m.set_position_target_local_ned_encode(123456, 1, 1, 8, mav.VELOCITY_YAWRATE_MASK, 0, 0, 0, .3, 0, 0, 0, 0, 0, 0, -.25).pack(m)
    assert mav.body_velocity(7, 1, 191, 123456, 1, 1, .3, -.25) == bytes(want)
    m.seq = 9
    want = m.heartbeat_encode(18, 8, 0, 0, 4, 3).pack(m)
    assert mav.heartbeat(9, 1) == bytes(want)
    link = mavutil.mavlink.MAVLink(None)
    link.robust_parsing = False
    got = link.parse_buffer(mav.body_velocity(1, 1, 191, 5, 1, 1, .8, .5))[0]
    assert got.get_type() == "SET_POSITION_TARGET_LOCAL_NED" and got.coordinate_frame == 8
    assert (round(got.vx, 3), got.vy, got.vz, round(got.yaw_rate, 3), got.type_mask) == (.8, 0, 0, .5, 1479)
    # the reference library's ATTITUDE is read by ours, with its boot timestamp
    fc = ref.MAVLink(None, srcSystem=4, srcComponent=1)
    packed = fc.attitude_encode(987654, .1, .2, .3, 0, 0, 0).pack(fc)
    assert mav.parse(bytes(packed)) == [{"kind": "attitude", "sysid": 4, "compid": 1, "time_boot_ms": 987654}]
    assert mav.parse(attitude(987654, sysid=4)) == mav.parse(bytes(packed))


def test_only_velocity_and_yaw_rate_are_used_and_zero_fields_survive_truncation():
    assert mav.VELOCITY_YAWRATE_MASK == 1479
    packet = mav.body_velocity(0, 1, 191, 0, 1, 1, 0.0, 0.0)
    assert packet[0] == 0xFD and packet[1] == 53                     # frame byte 8 is last, so nothing is cut
    assert struct.unpack("<I11fHBBB", packet[10:63])[4:7] == (0.0, 0.0, 0.0)


def test_parse_reads_px4_heartbeats_in_both_versions_and_skips_everything_else():
    hb = mav.parse(px4(offboard=True, armed=False, sysid=4))
    assert hb == [{"kind": "heartbeat", "sysid": 4, "compid": 1, "custom_mode": 6 << 16, "type": 2, "autopilot": 12, "base_mode": 0, "system_status": 4}]
    payload = struct.pack("<IBBBBB", 6 << 16, 2, 12, 0x80, 4, 3)
    head = bytes([9, 3, 2, 1, 0])
    v1 = b"\xfe" + head + payload + struct.pack("<H", mav.crc16(head + payload + bytes([50])))
    assert mav.parse(b"\x00junk" + v1 + mav.body_velocity(0, 1, 191, 0, 1, 1, .1, 0) + px4(sysid=2))[0]["base_mode"] == 0x80
    assert [m["kind"] for m in mav.parse(v1 + attitude(5) + px4(sysid=2))] == ["heartbeat", "attitude", "heartbeat"]
    bad = bytearray(px4()); bad[12] ^= 1
    assert mav.parse(bytes(bad)) == [] and mav.parse(px4()[:-1]) == [] and mav.parse(b"") == []
    bad = bytearray(attitude(5)); bad[10] ^= 1
    assert mav.parse(bytes(bad)) == []


# ---- flight-controller state ---------------------------------------------------------------
def hb(**kw):
    return mav.parse(px4(**kw))[0]


def att(boot_ms, **kw):
    return mav.parse(attitude(boot_ms, **kw))[0]


def test_link_accepts_only_the_pinned_px4_and_needs_heartbeat_and_advancing_attitude():
    link = FcLink(1, "10.0.0.5")
    for m in (hb(compid=190), hb(autopilot=3)):
        link.note(m, 1.0, ADDR)                                      # a ground station, another autopilot type
    link.note(hb(), 1.0, ("10.0.0.66", 14580))                       # right id, wrong host: not even a conflict
    link.note(hb(), 1.0, None)
    link.note(att(100), 1.0, ADDR)                                   # telemetry before any heartbeat pins nothing
    assert link.view(1.0) is None and link.address is None and link.attitude is None
    link.note(hb(offboard=False, armed=False), 1.0, ADDR)
    assert link.view(1.0) is None and "ATTITUDE" in link.problem(1.0)   # heartbeat alone is not liveness
    link.note(att(1000), 1.0, ADDR)
    assert link.view(1.1) == {"sysid": 1, "offboard": False, "armed": False, "address": ADDR}
    assert link.view(1.31) is None                                   # attitude 0.31 s old
    link.note(att(1000), 1.4, ADDR)                                  # a repeated timestamp is not progress
    assert link.view(1.4) is None
    link.note(att(1400), 1.4, ADDR); link.note(att(1500, sysid=2), 1.45, ADDR); link.note(att(1600), 1.45, ("10.0.0.5", 9))
    assert link.view(1.45) is not None and link.attitude == (1.4, 1400)   # other id / port: ignored
    assert link.max_gap == pytest.approx(0.4)
    link.note(att(2600), 2.6, ADDR)
    assert link.view(2.6) is None and "heartbeat" in link.problem(2.6)   # heartbeat 1.6 s old
    link.note(hb(), 3.0, ADDR); link.note(att(3000), 3.0, ADDR)
    link.note(hb(sysid=2), 3.1, ADDR)                                # another vehicle id from the pinned host
    assert link.view(3.2) is None
    link.note(hb(), 5.0, ADDR); link.note(att(5000), 5.0, ADDR)
    assert link.view(5.0)["offboard"] is True


def test_attitude_time_going_backwards_is_a_reboot_never_fresh_data():
    link = FcLink(1, "10.0.0.5")
    link.note(hb(), 1.0, ADDR); link.note(att(50000), 1.0, ADDR)
    assert link.view(1.0) is not None
    link.note(att(200), 1.1, ADDR)                                   # rebooted (or reordered old data)
    assert link.view(1.1) is None and "rebooted" in link.problem(1.1)
    link.note(att(300), 1.2, ADDR); link.note(hb(), 1.2, ADDR)
    assert link.view(1.2) is None                                    # unknown for the conflict period
    link.note(att(2000), 2.7, ADDR); link.note(hb(), 2.7, ADDR)
    assert link.view(2.7) is not None


def test_link_never_moves_to_another_sender_even_after_a_timeout():
    link = FcLink(1, "10.0.0.5")
    link.note(hb(), 1.0, ADDR); link.note(att(1), 1.0, ADDR)
    assert link.view(60.0) is None                                   # long silence
    link.note(hb(), 60.0, ("10.0.0.5", 14999)); link.note(att(9), 60.0, ("10.0.0.5", 14999))
    assert link.view(60.1) is None and link.address == ADDR
    link.note(hb(), 62.0, ADDR); link.note(att(62000), 62.0, ADDR)
    assert link.view(62.0)["address"] == ADDR
    wrong = FcLink(4, "127.0.0.1")
    wrong.note(hb(sysid=1), 1.0, ("127.0.0.1", 14583))
    assert wrong.view(1.0) is None and wrong.address is None         # wrong system id never becomes the destination


# ---- the gate: stream session ---------------------------------------------------------------
def test_nothing_is_streamed_until_an_operator_opens_a_session():
    g = FcGate()
    for i, request in enumerate(("off", "off", "off")):
        out = g.step(snap(i + 1, 0, request), 50, 50, FC)
        assert out["stream"] is False and out["state"] == "ready"
    g = FcGate()
    assert g.step(snap(1, 3, "fc_hold"), 50, 50, FC)["stream"] is False   # hold was already on at start-up
    out = g.step(snap(2, 3, "fc_hold"), 50, 50, FC)
    assert out["stream"] is False and out["state"] == "refused" and "press Nowhere" in out["reason"]
    assert g.step(snap(3, 4, "fc"), 50, 50, FC)["stream"] is False        # follow without a session: nothing
    assert g.step(snap(4, 5, "off"), 50, 50, FC)["state"] == "ready"
    assert g.step(snap(5, 6, "fc_hold"), 50, 50, FC)["stream"] is True


def test_session_survives_off_and_perception_faults_but_not_loss_of_the_flight_controller():
    g = granted()
    assert g.step(snap(4, 3, "off"), 50, 50, FC)["stream"] is True        # Nowhere: zeros keep flowing
    bad = snap(5, 4, "fc"); bad["onboard"]["valid"] = False
    out = g.step(bad, 50, 50, FC)
    assert out["stream"] is True and out["state"] == "holding" and out["forward_vel"] == 0
    assert g.step(None, None, None, FC)["stream"] is True                 # camera computer unreachable: zeros
    out = g.step(snap(6, 4, "fc"), 50, 50, None, "no heartbeat from the expected PX4 flight controller")
    assert out["stream"] is False and "stream stopped" in out["reason"]
    # astra-0120: the contact comes back (PX4 may have re-entered offboard by itself): still nothing
    for i in range(3):
        out = g.step(snap(7 + i, 4, "fc"), 50, 50, FC)
        assert out["stream"] is False and (out["forward_vel"], out["yaw_rate"]) == (0, 0)
    assert g.step(snap(10, 5, "fc"), 50, 50, FC)["stream"] is False       # a new follow press alone is not enough
    assert g.step(snap(11, 6, "off"), 50, 50, FC)["stream"] is False
    assert g.step(snap(12, 7, "fc_hold"), 50, 50, FC)["stream"] is True   # explicit new session
    assert g.step(snap(13, 8, "fc"), 50, 50, FC)["state"] == "sending"    # then an explicit new grant


@pytest.mark.parametrize("request_name", ["fc_hold", "fc"])
def test_a_press_made_while_the_flight_controller_was_silent_never_takes_effect(request_name):
    """astra-0114 finding 1, now for both acts: the generation is consumed when seen."""
    g = streaming() if request_name == "fc" else FcGate()
    if request_name == "fc_hold":
        g.step(snap(), 50, 50, FC)
    g.step(snap(3, 5, request_name), 50, 50, None)                        # pressed during the gap
    for i in range(3):
        out = g.step(snap(4 + i, 5, request_name), 50, 50, FC)
        assert out["stream"] is False and out["forward_vel"] == 0
    g2 = FcGate()
    assert g2.step(snap(1, 0), 50, 50, None)["stream"] is False           # baseline taken while silent
    assert g2.step(snap(2, 1, "fc_hold"), 50, 50, None)["stream"] is False
    assert g2.step(snap(3, 1, "fc_hold"), 50, 50, FC)["stream"] is False


def test_startup_and_detector_restart_never_grant_a_request_that_was_already_on():
    g = streaming()
    other = snap(5, 0, "fc"); other["stats"]["instance"] = other["transmit"]["instance"] = other["onboard"]["instance"] = "pi-2"
    out = g.step(other, 50, 50, FC)
    assert out["state"] == "holding" and out["stream"] is True and out["forward_vel"] == 0   # zeros continue, no grant
    assert g.step(dict(other, frame=dict(other["frame"], seq=6)), 50, 50, FC)["forward_vel"] == 0


def test_grant_needs_offboard_first_and_ends_when_offboard_ends():
    g = streaming(MANUAL)                                                 # the stream may start before offboard
    out = g.step(snap(3, 2, "fc"), 50, 50, MANUAL)
    assert out["stream"] and out["forward_vel"] == 0 and "not in offboard" in out["reason"]
    assert g.step(snap(4, 2, "fc"), 50, 50, FC)["forward_vel"] == 0       # entering offboard later does not start motion
    assert g.step(snap(5, 3, "fc_hold"), 50, 50, FC)["state"] == "holding"
    assert g.step(snap(6, 4, "fc"), 50, 50, FC)["state"] == "sending"
    out = g.step(snap(7, 4, "fc"), 50, 50, MANUAL)                        # pilot took over
    assert not out["stream"] and out["forward_vel"] == 0 and out["yaw_rate"] == 0 and "left offboard" in out["reason"]
    assert not g.step(snap(8, 4, "fc"), 50, 50, FC)["stream"]


@pytest.mark.parametrize("change,why", [
    (lambda s: s["onboard"].update(valid=False), "Pi command invalid"),
    (lambda s: s["onboard"].update(transmit=True), "unsent"),
    (lambda s: s["onboard"].update(vertical_vel=.1), "limits"),
    (lambda s: s["onboard"].update(forward_vel=.9), "limits"),
    (lambda s: s["onboard"].update(yaw_rate=float("nan")), "limits"),
    (lambda s: s["onboard"].update(target_id=8), "selection"),
    (lambda s: s["lock"].update(coasting=True), "selection"),
    (lambda s: s["stats"]["capabilities"].update(mount="fixed"), "mount"),
    (lambda s: s["transmit"].update(instance="other"), "no usable state"),
    (lambda s: s.pop("onboard"), "Pi command invalid"),
])
def test_any_doubt_ends_the_grant_with_zero_setpoints_and_no_resume(change, why):
    g = granted()
    bad = snap(4, 2, "fc"); change(bad)
    out = g.step(bad, 50, 50, FC)
    assert out["stream"] and out["state"] == "holding" and (out["forward_vel"], out["yaw_rate"]) == (0, 0) and why in out["reason"]
    assert g.step(snap(5, 2, "fc"), 50, 50, FC)["state"] == "holding"
    assert g.step(snap(6, 3, "fc_hold"), 50, 50, FC)["state"] == "holding"
    assert g.step(snap(7, 4, "fc"), 50, 50, FC)["state"] == "sending"


def test_stale_frozen_missing_or_other_target_all_hold():
    for age, progress in ((401, 50), (50, 401), (None, 50), (float("nan"), 50), (-1, 50)):
        assert granted().step(snap(4, 2, "fc"), age, progress, FC)["state"] == "holding"
    assert granted().step(None, None, None, FC)["forward_vel"] == 0
    g = granted()
    assert g.step(snap(4, 3, "off"), 50, 50, FC)["forward_vel"] == 0 and g.grant is None
    g = granted()
    assert g.step(snap(4, 3, "sim"), 50, 50, FC)["forward_vel"] == 0     # the other target is not ours
    g = granted(); g.step(snap(6, 2, "fc"), 50, 50, FC)
    assert g.step(snap(5, 2, "fc"), 50, 50, FC)["state"] == "holding"    # sequence backwards


# ---- the bridge tick -------------------------------------------------------------------------
class Sock:
    def __init__(self):
        self.inbox, self.sent = [], []

    def recvfrom(self, n):
        if not self.inbox:
            raise BlockingIOError
        return self.inbox.pop(0)

    def sendto(self, data, address):
        self.sent.append((data, address))


class Clock:
    def __init__(self, t=1.0):
        self.t = t

    def __call__(self):
        return self.t


def setpoints(sock):
    out = []
    for data, address in sock.sent:
        if int.from_bytes(data[7:10], "little") == mav.SET_POSITION_TARGET_LOCAL_NED:
            f = struct.unpack("<I11fHBBB", data[10:10 + data[1]].ljust(53, b"\0"))
            out.append((round(f[4], 3), round(f[11], 3), f[5], f[6], f[12], f[13], f[14], f[15], address))
    return out


def bridge(sysid=4, host=SIM_ADDR[0], **kw):
    s, c = Sock(), Clock()
    return s, c, Bridge(s, sysid, host, clock=c, **kw)


def feed(s, c, sysid=4, address=SIM_ADDR, **kw):
    s.inbox += [(px4(sysid=sysid, **kw), address), (attitude(int(c.t * 1000), sysid=sysid), address)]


def run(b, s, c, snapshot, dt=0.05, alive=True, **kw):
    c.t += dt
    if alive:
        feed(s, c, **kw)
    return b.tick(snapshot, 50, 50)


def flying():
    """Bridge with an open session and a grant, heartbeat and attitude arriving every tick."""
    s, c, b = bridge()
    feed(s, c); b.tick(snap(), 50, 50)
    assert run(b, s, c, snap(2, 1, "fc_hold"))["state"] == "holding"
    assert run(b, s, c, snap(3, 2, "fc"))["state"] == "sending"
    return s, c, b


def test_tick_sends_nothing_until_a_session_then_zero_then_the_command():
    s, c, b = bridge(label="SIMULATOR")
    assert b.tick(snap(), 50, 50)["stream"] is False and s.sent == []
    out = run(b, s, c, snap())
    assert out["fc"] == {"sysid": 4, "offboard": True, "armed": True} and out["state"] == "ready" and s.sent == []
    assert out["reason"].startswith("SIMULATOR: ")
    out = run(b, s, c, snap(2, 1, "fc_hold"))
    assert out["state"] == "holding" and setpoints(s) == [(0.0, 0.0, 0.0, 0.0, 1479, 4, 1, 8, SIM_ADDR)]
    assert sum(int.from_bytes(d[7:10], "little") == mav.HEARTBEAT for d, _ in s.sent) == 1
    out = run(b, s, c, snap(3, 2, "fc"))
    assert out["state"] == "sending" and setpoints(s)[-1][:2] == (.3, .2) and out["setpoints_sent"] == 2
    assert [d[4] for d, _ in s.sent] == [0, 1, 2]                    # packet sequence advances


def test_losing_fast_telemetry_stops_the_stream_within_the_short_limit_and_it_stays_stopped():
    """astra-0120: liveness is advancing ATTITUDE, not the 1 Hz heartbeat; no implicit restart."""
    s, c, b = flying()
    n = len(setpoints(s))
    for i in range(6):                                               # 0.30 s without any message from the FC
        run(b, s, c, snap(4 + i, 2, "fc"), alive=False)
    assert len(setpoints(s)) - n == 5 and all(p[:2] == (.3, .2) for p in setpoints(s)[n:])   # 0.05 .. 0.25 s old
    out = run(b, s, c, snap(10, 2, "fc"), alive=False)               # 0.35 s
    assert out["stream"] is False and "ATTITUDE" in out["reason"] and len(setpoints(s)) - n == 5
    for i in range(5):                                               # the FC is back, and in offboard again
        out = run(b, s, c, snap(11 + i, 2, "fc"))
        assert out["stream"] is False and out["fc"]["offboard"] is True
    assert len(setpoints(s)) - n == 5                                # not one packet, zero or otherwise
    run(b, s, c, snap(20, 3, "off"))
    assert run(b, s, c, snap(21, 4, "fc_hold"))["stream"] is True and setpoints(s)[-1][:2] == (0.0, 0.0)
    assert run(b, s, c, snap(22, 5, "fc"))["state"] == "sending"


def test_wrong_host_or_wrong_system_never_receives_anything():
    s, c, b = bridge(sysid=4, host="127.0.0.1")
    feed(s, c, address=("10.0.0.5", 14580)); feed(s, c, sysid=1, address=("127.0.0.1", 14583))
    assert b.tick(snap(), 50, 50)["fc"] is None
    for i in range(3):                                               # even a hold press: nobody to talk to
        assert run(b, s, c, snap(2 + i, 1 + i, "fc_hold"), alive=False)["stream"] is False
    assert s.sent == []
    c.t = 5.0; b.last_tick = 4.95
    feed(s, c, address=("127.0.0.1", 14583)); b.tick(snap(9, 9), 50, 50)
    run(b, s, c, snap(10, 10, "fc_hold"), address=("127.0.0.1", 14583))
    assert {a for _, a in s.sent} == {("127.0.0.1", 14583)}


def test_a_paused_loop_ends_the_session_before_it_sends_again():
    """astra-0114 finding 2 and astra-0120: after a stall not even zeros restart by themselves."""
    s, c, b = flying()
    n = len(setpoints(s))
    out = run(b, s, c, snap(4, 2, "fc"), dt=0.3)                     # loop paused 0.3 s; everything else fresh
    assert out["stream"] is False and "stalled" in out["reason"] and len(setpoints(s)) == n
    assert run(b, s, c, snap(5, 2, "fc"))["stream"] is False
    assert run(b, s, c, snap(6, 3, "fc"))["stream"] is False
    run(b, s, c, snap(7, 4, "off"))
    assert run(b, s, c, snap(8, 5, "fc_hold"))["stream"] is True
    assert run(b, s, c, snap(9, 6, "fc"))["state"] == "sending"
    out = run(b, s, c, snap(10, 6, "fc"), dt=-5, alive=False)                     # a clock that goes backwards is a stall too
    assert out["stream"] is False


def flooded(b, c, step):
    class Flood(Sock):
        calls = 0

        def recvfrom(self, n):
            Flood.calls += 1; c.t += step
            return px4(sysid=4) + attitude(int(c.t * 1000), sysid=4), SIM_ADDR
    old = b.sock; b.sock = f = Flood(); f.sent = old.sent
    return Flood


def test_a_flood_of_datagrams_cannot_hold_the_tick_and_is_stamped_with_fresh_time():
    """astra-0114 finding 4: bounded by count and by time."""
    s, c, b = bridge()
    F = flooded(b, c, 0.0001)
    out = b.tick(snap(), 50, 50)
    assert F.calls == 50 and out["fc"] is not None and s.sent == []
    assert b.link.seen[0] == pytest.approx(1.005)                    # stamped when read, not with the tick's start
    s2, c2, b2 = bridge()
    F2 = flooded(b2, c2, 0.008)
    b2.tick(snap(), 50, 50)
    assert F2.calls == 3                                             # 20 ms of reading, then on with the tick


def test_time_spent_inside_the_tick_ages_the_camera_state_before_anything_is_sent():
    """astra-0115: a stale non-zero command is never emitted, however the tick was delayed."""
    for slow_call, age, session_survives in ((0.5, 100, False), (0.2, 250, True), (0.02, 390, True)):
        s, c, b = flying()
        c.t += 0.05
        flooded(b, c, slow_call)                                     # one read takes this long, FC stays fresh
        n = len(setpoints(s))
        out = b.tick(snap(4, 2, "fc"), age, age)
        assert out["state"] != "sending" and all(p[:2] == (0.0, 0.0) for p in setpoints(s)[n:]), (slow_call, age)
        assert out["stream"] is session_survives                     # an over-long tick ends the session too
        b.sock = s; s.inbox.clear()
        # A stale frame is a perception pause: with the session intact the same grant resumes on a
        # fresh frame; when the over-long tick ended the session, nothing comes back.
        assert run(b, s, c, snap(5, 2, "fc"))["state"] == ("sending" if session_survives else "refused")
    s, c, b = flying(); c.t += 0.05; flooded(b, c, 0.001)            # a quick tick with honest ages still sends
    assert b.tick(snap(4, 2, "fc"), 100, 100)["state"] == "sending"


def test_receive_only_never_sends_and_never_reports_sending():
    s, c, b = bridge(receive_only=True)
    feed(s, c); b.tick(snap(), 50, 50)
    out = run(b, s, c, snap(2, 1, "fc_hold"))
    assert s.sent == [] and out["state"] == "refused" and out["stream"] is False and out["fc"]["offboard"] is True
    out = run(b, s, c, snap(3, 2, "fc"))
    assert out["state"] == "refused" and s.sent == [] and out["attitude_max_gap_ms"] == 50.0


# ---- start-up refusal and the status report --------------------------------------------------
class Args:
    def __init__(self, simulator=False, real=False, host="127.0.0.1", sysid=4, listen="127.0.0.1:14543"):
        self.receive_only = True
        self.simulator, self.real_flight_controller, self.fc_host, self.fc_sysid, self.listen = simulator, real, host, sysid, listen


def test_refuses_to_start_unless_exactly_one_endpoint_is_stated_twice():
    assert endpoint_kind(Args(simulator=True), {"GARUDA_SIM": "1"}) == "simulator"
    assert endpoint_kind(Args(real=True, host="10.0.0.5", sysid=1, listen="0.0.0.0:14540"), {"GARUDA_FC_BRIDGE": "1"}) == "flight controller"
    for args, env in ((Args(), {}), (Args(simulator=True), {}), (Args(), {"GARUDA_SIM": "1"}),
                      (Args(real=True), {"GARUDA_SIM": "1"}), (Args(simulator=True), {"GARUDA_FC_BRIDGE": "1"}),
                      (Args(real=True), {}), (Args(simulator=True, real=True), {"GARUDA_SIM": "1", "GARUDA_FC_BRIDGE": "1"}),
                      (Args(simulator=True), {"GARUDA_SIM": "1", "GARUDA_FC_BRIDGE": "1"})):
        with pytest.raises(SystemExit, match="REFUSED"):
            endpoint_kind(args, env)


def test_simulator_mode_is_loopback_only_and_the_flight_controller_must_be_pinned():
    """astra-0114 finding 3: the label alone does not isolate the endpoint."""
    sim, real = {"GARUDA_SIM": "1"}, {"GARUDA_FC_BRIDGE": "1"}
    for kw in (dict(host="10.42.0.50"), dict(listen="0.0.0.0:14543"), dict(listen="10.42.0.1:14543"),
               dict(host=None), dict(sysid=None), dict(sysid=0), dict(sysid=256), dict(sysid="4")):
        with pytest.raises(SystemExit, match="REFUSED"):
            endpoint_kind(Args(simulator=True, **kw), sim)
    for kw in (dict(host="127.0.0.1"), dict(host=None), dict(sysid=None)):
        with pytest.raises(SystemExit, match="REFUSED"):
            endpoint_kind(Args(real=True, **{"host": "10.0.0.5", "listen": "0.0.0.0:14540", **kw}), real)


def test_status_goes_to_the_real_send_switch_and_a_stalled_loop_cannot_say_sending():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "hosts", "pi5"))
    from mono_transmit import Transmit
    from urllib.parse import parse_qs, urlparse
    t = Transmit("pi-1"); clock = [10.0]

    def opener(req, timeout):
        q = parse_qs(urlparse(req.full_url).query)
        gen = q.get("generation", [None])[0]
        ack = t.report(q["target"][0], q["state"][0], q.get("reason", [""])[0], clock[0],
                       None if gen is None else int(gen), q.get("instance", [None])[0],
                       {"1": True, "0": False, None: None}[q.get("stream", [None])[0]])
        return io.BytesIO(json.dumps(dict(ack, ok=True)).encode())
    p = PiState("http://pi", opener)
    p.publish({"state": "holding", "reason": "start", "generation": None, "instance": None}, 10.0); p.send_status(10.0)
    assert t.snapshot(10.0)["listeners"]["fc"]["state"] == "ready"   # unbound: liveness only
    t.ask("fc_hold", "vehicle", 10.1); clock[0] = 10.4
    p.publish({"state": "holding", "reason": "zero stream only", "generation": 1, "instance": "pi-1"}, 10.4); p.send_status(10.4)
    assert t.snapshot(10.4)["listeners"]["fc"]["state"] == "holding"   # the hold request is addressed to the fc sender
    t.ask("fc", "vehicle", 10.5); clock[0] = 10.7
    p.publish({"state": "sending", "reason": "armed", "generation": 2, "instance": "pi-1"}, 10.7); p.send_status(10.7)
    assert t.snapshot(10.7)["listeners"]["fc"]["state"] == "sending"
    p.send_status(10.8)                                              # rate limited
    clock[0] = 11.3; p.send_status(11.3)                             # last publish 0.6 s old
    l = t.snapshot(11.3)["listeners"]["fc"]
    assert l["state"] == "refused" and l["streaming"] is False and "stalled" in l["reason"]
    assert "stalled" in p.take_error(("pi-1", 2))                    # and the loop is told to end its session
    t.off("selection changed"); clock[0] = 11.8
    p.publish({"state": "sending", "reason": "armed", "generation": 2, "instance": "pi-1"}, 11.8); p.send_status(11.8)
    assert p.take_error(("pi-1", 9)) is None                         # error about a superseded request is discarded
    p.send_status(12.1)
    assert "request changed" in p.take_error(("pi-1", 2))


# ---- astra-0121 ------------------------------------------------------------------------------
@pytest.mark.parametrize("age,progress", [(10000, 10000), (None, None), (float("nan"), 50), (50, 401), (-1, 50), (True, 50)])
def test_a_stale_hold_request_never_opens_a_stream_and_is_used_up(age, progress):
    g = FcGate(); g.step(snap(), 50, 50, FC)
    out = g.step(snap(2, 1, "fc_hold"), age, progress, FC)
    assert out["stream"] is False and out["state"] == "refused" and "stale" in out["reason"]
    out = g.step(snap(3, 1, "fc_hold"), 50, 50, FC)                       # a fresh frame, SAME rejected generation
    assert out["stream"] is False and out["state"] == "refused"
    g.step(snap(4, 2, "off"), 50, 50, FC)
    assert g.step(snap(5, 3, "fc_hold"), 50, 50, FC)["stream"] is True    # only a new, fresh press


def test_hold_needs_vehicle_mount_but_no_person_and_staleness_does_not_end_a_running_stream():
    g = FcGate(); g.step(snap(), 50, 50, FC)
    fixed = snap(2, 1, "fc_hold"); fixed["stats"]["capabilities"]["mount"] = "fixed"
    out = g.step(fixed, 50, 50, FC)
    assert out["stream"] is False and "mount" in out["reason"]
    assert g.step(snap(3, 1, "fc_hold"), 50, 50, FC)["stream"] is False
    g.step(snap(4, 2, "off"), 50, 50, FC)
    nobody = snap(5, 3, "fc_hold"); nobody["onboard"]["valid"] = False; nobody["lock"]["id"] = None; nobody["tracks"] = []
    assert g.step(nobody, 50, 50, FC)["stream"] is True                   # zero stream needs no person
    assert g.step(snap(6, 3, "fc_hold"), 10000, 10000, FC)["stream"] is True   # perception staleness: zeros continue
    assert g.step(snap(7, 4, "off"), 50, 50, FC)["stream"] is True
    assert g.step(snap(8, 5, "fc_hold"), 10000, 10000, FC)["stream"] is True   # a stale re-press cannot end it either


def test_no_stream_is_never_reported_as_holding():
    g = streaming()
    out = g.step(snap(3, 1, "fc_hold"), 50, 50, None, "no heartbeat from the expected PX4 flight controller")
    assert out["stream"] is False and out["state"] == "refused" and "stream stopped" in out["reason"]
    assert g.step(snap(4, 1, "fc_hold"), 50, 50, FC)["state"] == "refused"
    assert g.step(snap(5, 2, "off"), 50, 50, FC)["state"] == "ready"
    assert g.step(None, None, None, FC)["state"] == "ready"
    g = streaming()
    assert g.step(snap(3, 2, "off"), 50, 50, FC) ["state"] == "holding"   # holding only ever with a stream
    for out in (FcGate().step(snap(), 50, 50, None), FcGate().step(None, None, None, FC)):
        assert out["stream"] is False and out["state"] != "holding"


def test_stream_statement_reaches_the_switch_bound_and_survives_nowhere():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "hosts", "pi5"))
    from mono_transmit import Transmit
    from urllib.parse import parse_qs, urlparse
    t = Transmit("pi-1"); clock = [10.0]; seen = []

    def opener(req, timeout):
        q = parse_qs(urlparse(req.full_url).query); seen.append(q)
        gen = q.get("generation", [None])[0]
        ack = t.report(q["target"][0], q["state"][0], q.get("reason", [""])[0], clock[0], None if gen is None else int(gen),
                       q.get("instance", [None])[0], {"1": True, "0": False, None: None}[q.get("stream", [None])[0]])
        return io.BytesIO(json.dumps(dict(ack, ok=True)).encode())
    p = PiState("http://pi", opener)

    def say(now, **report):
        clock[0] = now; p.publish(report, now); p.send_status(now)
        return t.snapshot(now)["listeners"]["fc"]
    assert say(10.0, state="ready", reason="", generation=None, instance=None, stream=False)["streaming"] is None   # unbound says nothing
    t.ask("fc_hold", "vehicle", 10.1)
    l = say(10.4, state="holding", reason="zero stream only", generation=1, instance="pi-1", stream=True)
    assert (l["state"], l["streaming"]) == ("holding", True) and seen[-1]["stream"] == ["1"]
    t.ask("off", "vehicle", 10.5)                                    # Nowhere: generation 2
    assert t.snapshot(10.6)["listeners"]["fc"]["streaming"] is None  # the old statement is about generation 1
    l = say(10.8, state="holding", reason="zero stream only", generation=2, instance="pi-1", stream=True)
    assert (l["state"], l["streaming"]) == ("stale", True)           # follow is off, zeros still flow, and it says so
    l = say(11.2, state="refused", reason="stream stopped", generation=2, instance="pi-1", stream=False)
    assert l["streaming"] is False
    l = say(11.6, state="holding", reason="", generation=2, instance="another", stream=True)
    assert l["streaming"] is None                                    # another detector's statement
    with pytest.raises(ValueError):
        t.report("fc", "holding", "", 12.0, 2, "pi-1", "yes")


def test_signed_and_unknown_flag_frames_are_skipped_not_interpreted():
    good = px4(sysid=4)
    signed = bytearray(good); signed[2] = 1
    assert mav.parse(bytes(signed) + b"\0" * 13 + good) == mav.parse(good)   # signed frame skipped whole, next one read
    assert mav.parse(bytes(signed)) == []
    unknown = bytearray(good); unknown[2] = 2
    assert mav.parse(bytes(unknown)) == []
    assert [m["sysid"] for m in mav.parse(bytes(unknown) + px4(sysid=7))] == [7]


# ---- perception pause (owner, 2026-11-16) ----------------------------------------------------
def invalid(seq, why="valid"):
    bad = snap(seq, 2, "fc")
    if why == "valid":
        bad["onboard"].update(valid=False, yaw_rate=0.0, forward_vel=0.0)
    elif why == "coasting":
        bad["lock"]["coasting"] = True
    elif why == "identity":
        bad["stats"]["identity"] = dict(confirmed=False, state="tracking", target=7, frame_seq=seq, frame_epoch="camera-1", instance="pi-1")
    return bad


@pytest.mark.parametrize("why", ["valid", "coasting", "identity"])
def test_a_short_perception_dropout_sends_zero_and_resumes_for_the_same_person(why):
    g = granted()
    for i, t in enumerate((10.0, 10.5, 12.0, 14.9)):
        out = g.step(invalid(4 + i, why), 50, 50, FC, None, t)
        assert out["stream"] and out["state"] == "holding" and (out["forward_vel"], out["yaw_rate"]) == (0, 0)
        assert "resumes by itself" in out["reason"]
    out = g.step(snap(9, 2, "fc"), 50, 50, FC, None, 15.0)                 # seen again, same generation
    assert out["state"] == "sending" and (out["forward_vel"], out["yaw_rate"]) == (.3, .2)
    assert g.step(snap(10, 2, "fc"), 500, 50, FC, None, 15.1)["forward_vel"] == 0      # a late frame pauses too
    assert g.step(None, None, None, FC, None, 15.2)["forward_vel"] == 0                # so does an unreachable Pi
    assert g.step(snap(11, 2, "fc"), 50, 50, FC, None, 15.3)["state"] == "sending"


def test_a_long_dropout_ends_the_grant_and_needs_the_operator_again():
    g = granted()
    assert g.step(invalid(4), 50, 50, FC, None, 10.0)["state"] == "holding"
    out = g.step(invalid(5), 50, 50, FC, None, 15.01)                      # more than 5 s
    assert out["forward_vel"] == 0 and "press Flight controller: hold, then follow again" in out["reason"]
    assert g.step(snap(6, 2, "fc"), 50, 50, FC, None, 15.1)["state"] == "holding"   # the person is back: nothing
    assert g.step(snap(7, 3, "fc_hold"), 50, 50, FC, None, 15.2)["state"] == "holding"
    assert g.step(snap(8, 4, "fc"), 50, 50, FC, None, 15.3)["state"] == "sending"
    g = granted(); g.step(invalid(4), 50, 50, FC, None, 10.0)
    assert g.step(invalid(5), 50, 50, FC, None, 9.0)["state"] == "holding" and g.grant is None   # clock backwards: no wait


def test_a_pause_never_resumes_for_another_person_camera_run_or_after_a_real_fault():
    g = granted(); g.step(invalid(4), 50, 50, FC, None, 10.0)
    other = snap(5, 2, "fc"); other["onboard"]["target_id"] = 8; other["lock"]["id"] = 8; other["tracks"][0]["id"] = 8
    other["stats"]["identity"] = None
    out = g.step(other, 50, 50, FC, None, 10.2)
    assert out["forward_vel"] == 0 and "changed" in out["reason"] and g.grant is None
    assert g.step(snap(6, 2, "fc"), 50, 50, FC, None, 10.3)["state"] == "holding"
    g = granted(); g.step(invalid(4), 50, 50, FC, None, 10.0)
    epoch = snap(5, 2, "fc"); epoch["frame"]["epoch"] = epoch["onboard"]["frame_epoch"] = "camera-2"
    assert g.step(epoch, 50, 50, FC, None, 10.2)["forward_vel"] == 0 and g.grant is None
    for change in (lambda s: s["onboard"].update(forward_vel=.9), lambda s: s["onboard"].update(vertical_vel=.1),
                   lambda s: s["stats"]["capabilities"].update(mount="fixed")):
        g = granted(); bad = snap(4, 2, "fc"); change(bad)
        out = g.step(bad, 50, 50, FC, None, 10.0)                          # not a perception dropout: at once
        assert out["forward_vel"] == 0 and g.grant is None and "resumes" not in out["reason"]
    g = granted(); g.step(invalid(4), 50, 50, FC, None, 10.0)
    assert g.step(invalid(5), 50, 50, MANUAL, None, 10.1)["forward_vel"] == 0 and g.grant is None   # pilot took over
    g = granted(); g.step(invalid(4), 50, 50, FC, None, 10.0)
    assert g.step(snap(5, 2, "fc"), 50, 50, None, "no heartbeat", 10.1)["stream"] is False
    assert g.step(snap(6, 2, "fc"), 50, 50, FC, None, 10.2)["stream"] is False                    # session gone, no resume


def test_the_transient_reasons_are_exactly_what_the_validator_says():
    from fc_gate import TRANSIENT
    from live_onboard import validate
    produced = set()
    produced.add(validate(snap(1, 0), 500, 50)[1])
    for why in ("valid", "coasting", "identity"):
        produced.add(validate(invalid(1, why), 50, 50)[1])
    mismatch = snap(1, 0); mismatch["onboard"]["frame_seq"] = 99
    produced.add(validate(mismatch, 50, 50)[1])
    weak = snap(1, 0); weak["tracks"][0]["conf"] = .2
    produced.add(validate(weak, 50, 50)[1])
    assert produced == set(TRANSIENT)


def test_bridge_tick_pauses_and_resumes_on_its_own_clock():
    s, c, b = flying()
    n = len(setpoints(s))
    for i in range(4):
        assert run(b, s, c, invalid(4 + i))["state"] == "holding"
    assert all(p[:2] == (0.0, 0.0) for p in setpoints(s)[n:]) and len(setpoints(s)) == n + 4
    assert run(b, s, c, snap(8, 2, "fc"))["state"] == "sending" and setpoints(s)[-1][:2] == (.3, .2)
    for i in range(104):                                             # more than 5 s of dropout at 20 Hz
        out = run(b, s, c, invalid(9 + i))
    assert out["state"] == "holding" and "then follow again" in out["reason"]
    assert run(b, s, c, snap(200, 2, "fc"))["state"] == "holding"


def test_valid_recovery_after_deadline_does_not_revive_grant():
    g = granted()
    for t in (10.0, 12.0, 14.99):
        g.step(invalid(4), 50, 50, FC, None, t)
    assert g.step(snap(5, 2, "fc"), 50, 50, FC, None, 15.01)["state"] == "holding"
    assert g.grant is None


def test_missing_camera_cannot_hide_offboard_exit():
    g = granted()
    g.step(None, None, None, MANUAL, None, 10)
    assert g.grant is None
    assert not g.step(snap(5, 2, "fc"), 50, 50, FC, None, 10.1)["stream"]


@pytest.mark.parametrize("boundary", ["target", "epoch", "source", "limit", "mount", "sequence"])
def test_invalid_camera_cannot_hide_known_boundary(boundary):
    g = granted(); bad = invalid(4)
    if boundary == "target": bad["lock"]["id"] = bad["onboard"]["target_id"] = 8
    if boundary == "epoch": bad["frame"]["epoch"] = "another-camera"
    if boundary == "source": bad["onboard"]["range_source"] = "stereo"
    if boundary == "limit": bad["onboard"]["forward_vel"] = 1.0
    if boundary == "mount": bad["stats"]["capabilities"]["mount"] = "fixed"
    if boundary == "sequence": bad["frame"]["seq"] = 1
    g.step(bad, 50, 50, FC, None, 10)
    assert g.grant is None
    assert g.step(snap(5, 2, "fc"), 50, 50, FC, None, 10.1)["state"] == "holding"

@pytest.mark.parametrize("interruption", [dict(armed=False), dict(offboard=False)])
@pytest.mark.parametrize("paused", [False, True])
def test_transient_authority_loss_in_same_receive_batch_stops_all_packets(interruption, paused):
    sock, clock, bridge = flying()
    if paused:
        run(bridge, sock, clock, invalid(4))
        assert bridge.gate.paused is not None
    packets = len(sock.sent)
    feed(sock, clock, **interruption)
    out = run(bridge, sock, clock, snap(4, 3, "fc_hold"))
    assert out["fc"]["armed"] and out["fc"]["offboard"]  # latest heartbeat recovered
    assert not out["stream"] and len(sock.sent) == packets
    for seq, gen, request in [(5, 3, "fc_hold"), (6, 4, "fc")]:
        assert not run(bridge, sock, clock, snap(seq, gen, request))["stream"]
    assert len(sock.sent) == packets


def test_disarmed_follow_press_cannot_grant_on_rearm():
    disarmed = dict(FC, armed=False)
    gate = streaming(disarmed)
    assert gate.step(snap(3, 2, "fc"), 50, 50, disarmed)["state"] == "holding"
    assert gate.grant is None
    assert gate.step(snap(4, 2, "fc"), 50, 50, FC)["forward_vel"] == 0


def test_real_endpoint_rejected_before_opening_sockets_and_sim_requires_literal_loopback():
    from fc_bridge import main
    with pytest.raises(SystemExit, match="Only --receive-only"):
        main(["--real-flight-controller", "--fc-host", "192.0.2.1", "--fc-sysid", "4",
              "--listen", "0.0.0.0:0"], {"GARUDA_FC_BRIDGE": "1"})
    with pytest.raises(SystemExit, match="loopback"):
        endpoint_kind(Args(simulator=True, host="127.example.test"), {"GARUDA_SIM": "1"})

def test_direct_bridge_to_non_loopback_remains_silent_with_valid_requests():
    sock, clock, observer = bridge(host=ADDR[0])
    for seq, request in enumerate(("off", "fc_hold", "fc", "off", "fc_hold", "fc"), 1):
        out = run(observer, sock, clock, snap(seq, seq, request), address=ADDR)
        assert out["fc"]["offboard"] and out["fc"]["armed"]
        assert not out["stream"] and out["state"] != "sending"
    assert sock.sent == []

def test_hold_request_first_seen_after_stall_is_consumed_without_reopening_stream():
    sock, clock, bridge = flying()
    before = len(sock.sent)
    assert not run(bridge, sock, clock, snap(4, 3, "fc_hold"), dt=.4)["stream"]
    assert not run(bridge, sock, clock, snap(5, 3, "fc_hold"))["stream"]
    assert not run(bridge, sock, clock, snap(6, 4, "fc"))["stream"]
    assert len(sock.sent) == before

def test_paused_old_generation_cannot_resume_after_batched_disarm_and_rearm():
    sock, clock, bridge = flying()
    run(bridge, sock, clock, invalid(4))
    before = len(sock.sent)
    feed(sock, clock, armed=False)
    for seq in range(5, 10):
        out = run(bridge, sock, clock, snap(seq, 2, "fc"))
        assert not out["stream"] and bridge.gate.grant is None
    assert len(sock.sent) == before
