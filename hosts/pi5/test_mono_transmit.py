import pytest

from mono_transmit import Transmit


def test_default_is_off_and_nothing_listens():
    s = Transmit().snapshot(10.0)
    assert s["request"] == "off" and s["generation"] == 0
    assert s["listeners"] == {t: {"alive": False, "state": None, "streaming": None, "reason": None, "age_ms": None} for t in ("sim", "fc")}


def test_request_needs_vehicle_mount_and_a_live_listener():
    t = Transmit()
    with pytest.raises(ValueError, match="nothing is listening"):
        t.ask("fc", "vehicle", 1.0)
    t.report("fc", "ready", "", 1.0)
    with pytest.raises(ValueError, match="mount"):
        t.ask("fc", "fixed", 1.1)
    with pytest.raises(ValueError, match="nothing is listening"):
        t.ask("sim", "vehicle", 1.1)                # the other target's listener does not count
    with pytest.raises(ValueError, match="target"):
        t.ask("everything", "vehicle", 1.1)
    t.ask("fc", "vehicle", 1.2)
    assert t.snapshot(1.3)["request"] == "fc" and t.generation == 1
    t.ask("fc", "vehicle", 1.4)                      # repeating the same request is not a new one
    assert t.generation == 1


def test_stale_listener_cannot_be_requested():
    t = Transmit()
    t.report("sim", "ready", "", 1.0)
    with pytest.raises(ValueError, match="nothing is listening"):
        t.ask("sim", "vehicle", 3.5)
    with pytest.raises(ValueError, match="nothing is listening"):
        t.ask("sim", "vehicle", 0.5)                 # a heartbeat from the future is not alive


def test_request_drops_when_the_listener_goes_quiet_and_does_not_return():
    t = Transmit()
    t.report("sim", "ready", "", 1.0); t.ask("sim", "vehicle", 1.1)
    assert t.snapshot(2.9)["request"] == "sim"
    s = t.snapshot(3.2)
    assert s["request"] == "off" and "stopped answering" in s["reason"] and s["generation"] == 2
    assert t.report("sim", "ready", "", 3.3) == {"request": "off", "generation": 2, "instance": ""}   # back, but not resumed
    assert t.snapshot(3.4)["request"] == "off"


def test_off_and_boundaries_change_the_generation_once():
    t = Transmit()
    t.report("fc", "ready", "", 1.0); t.ask("fc", "vehicle", 1.0)
    t.off("camera mount changed")
    assert (t.request, t.generation, t.reason) == ("off", 2, "camera mount changed")
    t.off("again")
    assert t.generation == 2
    t.ask("off", "vehicle", 1.0)
    assert t.request == "off" and t.generation == 2


def test_report_validates_and_returns_the_current_request():
    t = Transmit("det1")
    for bad in (("drone", "ready"), ("fc", "flying")):
        with pytest.raises(ValueError):
            t.report(bad[0], bad[1], "", 1.0)
    with pytest.raises(ValueError):
        t.report("fc", "ready", "", float("nan"))
    for kw in ({}, {"generation": 1}, {"instance": "det1"}, {"generation": "1", "instance": "det1"}, {"generation": True, "instance": "det1"}):
        with pytest.raises(ValueError, match="required"):
            t.report("fc", "sending", "", 1.0, **kw)      # a statement about a request must say which
    t.report("fc", "ready", "", 1.0); t.ask("fc", "vehicle", 1.0)
    assert t.report("fc", "sending", "x" * 500, 1.5, 1, "det1") == {"request": "fc", "generation": 1, "instance": "det1"}
    s = t.snapshot(1.6)
    assert s["instance"] == "det1" and s["listeners"]["fc"]["state"] == "sending" and s["listeners"]["fc"]["age_ms"] == 100
    assert len(s["listeners"]["fc"]["reason"]) == 120


def test_late_heartbeat_cannot_keep_or_revive_a_request_without_any_snapshot():
    """astra-0104 finding 1: expiry runs before the heartbeat is renewed."""
    t = Transmit("det1")
    t.report("sim", "ready", "", 1.0); t.ask("sim", "vehicle", 1.1)
    assert t.report("sim", "ready", "", 4.0) == {"request": "off", "generation": 2, "instance": "det1"}
    assert t.snapshot(4.1)["request"] == "off"
    t.ask("sim", "vehicle", 4.2)                         # only a new operator request, with a new generation
    assert (t.request, t.generation) == ("sim", 3)
    t2 = Transmit("det1")
    t2.report("fc", "ready", "", 1.0); t2.ask("fc", "vehicle", 1.1); t2.report("sim", "ready", "", 3.9)
    with pytest.raises(ValueError, match="nothing is listening"):
        t2.ask("fc", "vehicle", 4.0)                     # ask expires first too
    assert t2.request == "off"


def test_sending_counts_only_for_the_current_request_generation_and_instance():
    """astra-0104 finding 2: an old or foreign "sending" never labels a new request."""
    t = Transmit("det1")
    t.report("sim", "sending", "", 1.0, 0, "det1")       # statement made before any request
    t.ask("sim", "vehicle", 1.1)
    assert t.snapshot(1.2)["listeners"]["sim"]["state"] == "stale"
    t.report("sim", "sending", "", 1.3, 1, "det1")
    assert t.snapshot(1.4)["listeners"]["sim"]["state"] == "sending"
    t.ask("off", "vehicle", 1.5); t.ask("sim", "vehicle", 1.6)             # off, on again: generation 3
    assert t.generation == 3 and t.snapshot(1.7)["listeners"]["sim"]["state"] == "stale"
    t.report("sim", "sending", "", 1.8, 3, "another-detector")
    assert t.snapshot(1.9)["listeners"]["sim"]["state"] == "stale"
    t.report("sim", "holding", "no person", 2.0, 3, "det1")
    assert t.snapshot(2.1)["listeners"]["sim"]["state"] == "holding"
    t.report("fc", "sending", "", 2.0, 3, "det1")        # right generation, but fc is not the request
    assert t.snapshot(2.1)["listeners"]["fc"]["state"] == "stale"
    t.report("sim", "ready", "", 2.2)
    assert t.snapshot(2.3)["listeners"]["sim"]["state"] == "ready"


def test_hold_and_follow_are_separate_requests_addressed_to_the_same_sender():
    t = Transmit("det1")
    with pytest.raises(ValueError, match="nothing is listening"):
        t.ask("fc_hold", "vehicle", 1.0)
    t.report("sim", "ready", "", 1.0)
    with pytest.raises(ValueError, match="flight-controller bridge"):
        t.ask("fc_hold", "vehicle", 1.0)                 # the simulator lab is not the bridge
    t.report("fc", "ready", "", 1.0)
    t.ask("fc_hold", "vehicle", 1.1)
    assert (t.request, t.generation) == ("fc_hold", 1)
    t.report("fc", "holding", "zero stream only", 1.2, 1, "det1")
    assert t.snapshot(1.3)["listeners"]["fc"]["state"] == "holding"
    t.ask("fc", "vehicle", 1.4)                          # follow is a new generation
    assert (t.request, t.generation) == ("fc", 2)
    assert t.snapshot(1.5)["listeners"]["fc"]["state"] == "stale"      # the hold-time statement does not carry over
    t.report("fc", "sending", "", 1.6, 2, "det1")
    assert t.snapshot(1.7)["listeners"]["fc"]["state"] == "sending"
    assert t.snapshot(3.7)["request"] == "off"           # the bridge went quiet: the request drops for both kinds
    t.report("fc", "ready", "", 4.0); t.ask("fc_hold", "vehicle", 4.0)
    assert t.snapshot(6.1)["request"] == "off"
