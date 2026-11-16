import math, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from onboard_command import OnboardConfig, OnboardFollower, command, limits

CFG = OnboardConfig()


def obs(**over):
    base = {"frame_w": 1280, "frame_h": 720, "age_ms": 80, "identity": None, "range_source": "stereo",
            "target": {"id": 7, "x": 540, "y": 150, "w": 200, "h": 400, "conf": 0.9, "coasting": False},
            "stereo": {"status": "ok", "z_m": 4.5, "reason": "ok"}}
    base.update(over)
    return base


def box(**over):
    return dict(obs()["target"], **over)


def test_centred_far_person_gives_forward_only():
    c = command(obs())
    assert c["valid"] and c["yaw_rate"] == 0.0 and c["vertical_vel"] == 0.0
    assert c["forward_vel"] == pytest.approx(min(CFG.max_forward_vel, CFG.range_gain * (4.5 - CFG.gap_m)))
    assert c["range_m"] == 4.5 and c["range_source"] == "stereo" and c["forward_reason"] == "ok"


def test_signs_right_is_positive_yaw_near_is_backward():
    right = command(obs(target=box(x=800)))
    left = command(obs(target=box(x=200)))
    assert right["yaw_rate"] > 0 > left["yaw_rate"]
    near = command(obs(stereo={"status": "ok", "z_m": 1.2, "reason": "ok"}))
    assert -CFG.max_back_vel <= near["forward_vel"] < 0
    at_gap = command(obs(stereo={"status": "ok", "z_m": CFG.gap_m + 0.1, "reason": "ok"}))
    assert at_gap["forward_vel"] == 0.0 and at_gap["valid"]


def test_limits_are_never_exceeded():
    for x in (-500, 0, 400, 1000, 1500):
        for z in (0.8, 1.0, 3.0, 12.0):
            c = command(obs(target=box(x=x), stereo={"status": "ok", "z_m": z, "reason": "ok"}))
            assert abs(c["yaw_rate"]) <= CFG.max_yaw_rate
            assert -CFG.max_back_vel <= c["forward_vel"] <= CFG.max_forward_vel
            assert c["vertical_vel"] == 0.0


@pytest.mark.parametrize("change,reason", [
    ({"target": None}, "no person selected"),
    ({"age_ms": 401}, "stale frame"), ({"age_ms": None}, "stale frame"), ({"age_ms": -1}, "stale frame"),
    ({"age_ms": float("nan")}, "stale frame"),
    ({"frame_w": 0}, "no frame"),
    ({"identity": False}, "identity not confirmed"),
    ({"range_source": "mono_net"}, "unknown distance source"),
])
def test_holds(change, reason):
    c = command(obs(**change))
    assert c == dict(c, valid=False, reason=reason, yaw_rate=0.0, forward_vel=0.0, vertical_vel=0.0)


@pytest.mark.parametrize("change,reason", [
    ({"coasting": True}, "selected person not seen in this frame"),
    ({"conf": 0.2}, "low confidence"),
    ({"h": 20}, "implausible box size"), ({"h": 719}, "implausible box size"),
    ({"w": float("inf")}, "unusable box"), ({"x": None}, "unusable box"), ({"conf": True}, "unusable box"),
])
def test_target_holds(change, reason):
    c = command(obs(target=box(**change)))
    assert not c["valid"] and c["reason"] == reason and c["forward_vel"] == 0.0 and c["yaw_rate"] == 0.0
    assert c["target_id"] == 7


@pytest.mark.parametrize("stereo,why", [
    (None, "no stereo distance"),
    ({"status": "invalid", "z_m": None, "reason": "cluster_too_spread"}, "no stereo distance (cluster_too_spread)"),
    ({"status": "ok", "z_m": float("nan"), "reason": "ok"}, "no stereo distance"),
    ({"status": "ok", "z_m": 0.3, "reason": "ok"}, "stereo distance outside the usable span"),
    ({"status": "ok", "z_m": 40.0, "reason": "ok"}, "stereo distance outside the usable span"),
])
def test_stereo_missing_never_falls_back_to_box_size(stereo, why):
    # A small box (looks far) must NOT produce forward motion when the stereo number is missing.
    c = command(obs(stereo=stereo, target=box(x=900, h=120, y=300)))
    assert c["valid"] and c["forward_vel"] == 0.0 and c["forward_reason"] == why and c["range_m"] is None
    assert c["yaw_rate"] > 0                      # turning still follows the person


def test_no_distance_source_turns_only():
    c = command(obs(range_source="none", target=box(x=100)))
    assert c["valid"] and c["forward_vel"] == 0.0 and c["forward_reason"] == "no distance source" and c["yaw_rate"] < 0


def test_box_height_source_ignores_stereo_and_uses_size():
    small = command(obs(range_source="box_height", target=box(h=180, y=250), stereo={"status": "ok", "z_m": 1.0}))
    big = command(obs(range_source="box_height", target=box(h=600, y=60)))
    assert small["forward_vel"] > 0 and big["forward_vel"] < 0
    assert small["range_m"] is None and small["range_source"] == "box_height"


@pytest.mark.parametrize("y,h", [(0, 200), (520, 200), (3, 200), (517, 200)])
def test_box_cut_by_top_or_bottom_edge_never_advances(y, h):
    c = command(obs(range_source="box_height", target=box(y=y, h=h)))
    assert c["valid"] and c["forward_vel"] == 0.0 and c["forward_reason"] == "box cut by the frame edge"


def test_no_advance_while_far_off_centre_but_backing_away_is_allowed():
    c = command(obs(target=box(x=1050)))
    assert c["forward_vel"] == 0.0 and c["forward_reason"] == "turning toward the person first" and c["yaw_rate"] > 0
    near = command(obs(target=box(x=1050), stereo={"status": "ok", "z_m": 1.0, "reason": "ok"}))
    assert near["forward_vel"] < 0


def test_follower_ramps_and_holds_at_once():
    f = OnboardFollower()
    first = f.step(obs(), 10.0)
    assert first["valid"] and first["forward_vel"] == 0.0
    prev, t = 0.0, 10.0
    for _ in range(40):
        t += 1 / 15
        out = f.step(obs(), t)
        assert out["forward_vel"] - prev <= CFG.max_accel / 15 + 1e-9
        prev = out["forward_vel"]
    assert prev == pytest.approx(CFG.max_forward_vel)
    held = f.step(obs(target=box(coasting=True)), t + 1 / 15)
    assert not held["valid"] and held["forward_vel"] == 0.0 and held["yaw_rate"] == 0.0
    again = f.step(obs(), t + 2 / 15)
    assert again["forward_vel"] <= CFG.max_accel / 15 + 1e-9        # starts from zero, not from the old speed


def test_follower_restarts_after_a_time_gap_or_clock_step_back():
    f = OnboardFollower()
    t = 0.0
    for _ in range(40):
        t += 1 / 15
        f.step(obs(), t)
    assert f.step(obs(), t + 5.0)["forward_vel"] == 0.0
    for _ in range(40):
        t += 1 / 15
        f.step(obs(), t + 5.0)
    assert f.step(obs(), 1.0)["forward_vel"] == 0.0
    assert f.step(obs(), float("nan"))["valid"] is False


def test_withdrawn_forward_request_stops_at_once_but_yaw_continues():
    f = OnboardFollower()
    t = 0.0
    for _ in range(40):
        t += 1 / 15
        f.step(obs(target=box(x=700)), t)
    out = f.step(obs(target=box(x=700), stereo={"status": "invalid", "z_m": None, "reason": "no_pair"}), t + 1 / 15)
    assert out["valid"] and out["forward_vel"] == 0.0 and out["yaw_rate"] > 0


def test_limits_report():
    assert limits()["max_forward_vel"] == CFG.max_forward_vel and "gap_m" in limits()
    assert all(math.isfinite(v) for v in limits().values())


@pytest.mark.parametrize("lost", [None, {"status": "invalid", "z_m": None, "reason": "no_pair"},
                                  {"status": "ok", "z_m": 0.3, "reason": "ok"}, {"status": "ok", "z_m": 40.0, "reason": "ok"}])
@pytest.mark.parametrize("z", [1.0, 6.0])
def test_losing_the_distance_stops_motion_at_once_in_both_directions(lost, z):
    """astra-0091 finding 1: backing away at full speed, then the stereo number disappears."""
    f = OnboardFollower()
    t, out = 0.0, None
    for _ in range(30):
        t += 1 / 15
        out = f.step(obs(stereo={"status": "ok", "z_m": z, "reason": "ok"}), t)
    assert out["forward_vel"] == pytest.approx(-CFG.max_back_vel if z < CFG.gap_m else CFG.max_forward_vel)
    gone = f.step(obs(stereo=lost), t + 1 / 15)
    assert gone["valid"] and gone["forward_vel"] == 0.0 and gone["forward_reason"] != "ok"
    back = f.step(obs(stereo={"status": "ok", "z_m": z, "reason": "ok"}), t + 2 / 15)
    assert abs(back["forward_vel"]) <= CFG.max_accel / 15 + 1e-9          # and restarts from zero


def test_reaching_the_gap_still_ramps_down():
    f = OnboardFollower()
    t = 0.0
    for _ in range(30):
        t += 1 / 15
        f.step(obs(), t)
    eased = f.step(obs(stereo={"status": "ok", "z_m": CFG.gap_m, "reason": "ok"}), t + 1 / 15)
    assert 0 < eased["forward_vel"] < CFG.max_forward_vel and eased["forward_reason"] == "ok"
