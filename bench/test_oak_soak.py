"""Soak summariser tests. Synthetic rows; no network, no device."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import oak_soak as m  # noqa: E402

CAP0 = {"delivered": 0, "no_new_frame": 0, "rejected": 0, "stale": 0, "unavailable": 0,
        "epoch_changes": 0, "depth_absent": 0}


def state(seq, inferences=None, instance="P1", epoch="E:0", age=100, stereo=None, temp=60.0,
          fps=12.5, lock=7, cls="person"):
    stats = {"fps": fps, "temp_c": temp}
    if inferences is not None:
        stats.update(inferences=inferences, instance=instance)
    s = {"age_ms": age, "frame": {"seq": seq, "epoch": epoch, "source": "oak-rgb"},
         "stats": stats, "tracks": [{}], "lock": {"id": lock, "class": cls},
         "distance": {"valid": True, "est_m": 2.0}}
    if stereo is not None:
        s["stereo"] = stereo
    return s


def ok(z, **cap):
    return {"status": "ok", "reason": "ok", "z_m": z, "coverage": .8, "support": .7,
            "capture": {**CAP0, **cap}}


def rows_from(states, throttled="0x0", cpu=None):
    return [m.sample_from_state(s, float(i), {"throttled": throttled, "vcgencmd_temp_c": 61.0}, cpu)
            for i, s in enumerate(states)]


# ---- R1: processed rate ------------------------------------------------------------------

def test_capture_15hz_inference_3hz_polling_1hz():
    states = [state(seq=15 * i, inferences=3 * i) for i in range(11)]
    r = m.summarise(rows_from(states))
    assert r["processed_fps"]["value"] == pytest.approx(3.0)
    assert r["processed_fps"]["inferences_completed"] == 30
    assert r["capture_sequence"]["advance"] == 150          # reported, but not as fps
    assert not any("fps" in key for key in r["capture_sequence"])


def test_processed_rate_is_unknown_without_the_inference_counter():
    r = m.summarise(rows_from([state(seq=15 * i) for i in range(5)]))
    assert r["processed_fps"]["value"] is None
    assert "unknown" in r["processed_fps"]["source"]
    assert r["capture_sequence"]["advance"] == 60


def test_process_restart_splits_the_rate_and_reports_uncovered_time():
    states = [state(0, 0, "P1"), state(15, 3, "P1"), state(30, 6, "P1"),
              state(45, 0, "P2"), state(60, 3, "P2")]
    r = m.summarise(rows_from(states))
    assert r["segments"] == 2 and r["process_instances_seen"] == 2
    assert r["processed_fps"]["inferences_completed"] == 9
    assert r["processed_fps"]["covered_s"] == 3.0 and r["processed_fps"]["uncovered_s"] == 1.0
    assert r["processed_fps"]["value"] == pytest.approx(3.0)


def test_counter_going_backwards_without_instance_change_is_still_a_new_segment():
    states = [state(0, 100, "P1"), state(15, 103, "P1"), state(30, 2, "P1"), state(45, 5, "P1")]
    r = m.summarise(rows_from(states))
    assert r["segments"] == 2 and r["processed_fps"]["inferences_completed"] == 6


def test_distinct_observed_frames_is_reported_separately():
    r = m.summarise(rows_from([state(5, 1), state(5, 1), state(9, 2)]))
    assert r["distinct_frames_observed_in_snapshots"] == 2


# ---- R5: throttle, temperature, counters -------------------------------------------------

def test_parse_throttled_separates_current_from_historical():
    assert m.parse_throttled("0x0") == {"raw": 0, "now": [], "past": []}
    p = m.parse_throttled("0x80008")
    assert p["now"] == ["soft_temp_limit_now"] and p["past"] == ["soft_temp_limit_occurred"]
    assert m.parse_throttled("0x50000")["now"] == []
    assert m.parse_throttled("0x50005")["now"] == ["undervoltage_now", "throttled_now"]
    for bad in (None, "", "junk", "0xZZ"):
        assert m.parse_throttled(bad) is None


def test_historical_flags_alone_are_not_counted_as_current_throttling():
    r = m.summarise(rows_from([state(i, i) for i in range(4)], throttled="0x80000"))
    t = r["local"]["throttle"]
    assert t["samples_with_any_now_flag"] == 0
    assert t["past_flags_at_start"] == ["soft_temp_limit_occurred"]
    assert t["past_flags_newly_set_during_run"] == []
    assert all(v["first_seen_s"] is None for v in t["now_flags"].values())


def test_current_flag_first_seen_and_newly_latched_history():
    rows = rows_from([state(i, i) for i in range(5)])
    rows[3]["throttled"] = "0x80008"
    rows[4]["throttled"] = "0x80000"
    t = m.summarise(rows)["local"]["throttle"]
    assert t["now_flags"]["soft_temp_limit_now"] == {"samples": 1, "first_seen_s": 3.0}
    assert t["samples_with_any_now_flag"] == 1
    assert t["past_flags_newly_set_during_run"] == ["soft_temp_limit_occurred"]


def test_unreadable_flags_are_unknown_not_clean():
    rows = rows_from([state(i, i) for i in range(4)])
    rows[1]["throttled"] = None
    rows[2]["throttled"] = "garbage"
    t = m.summarise(rows)["local"]["throttle"]
    assert (t["known_samples"], t["unknown_samples"]) == (2, 2)
    none = [m.sample_from_state(state(i, i), float(i), None) for i in range(3)]
    t = m.summarise(none)["local"]["throttle"]
    assert t == {"known_samples": 0, "unknown_samples": 3}


def test_both_temperature_sources_are_summarised_with_provenance():
    rows = rows_from([state(i, i, temp=60.0 + i) for i in range(3)])
    rows[2]["vcgencmd_temp_c"] = 70.5
    r = m.summarise(rows)
    assert r["local"]["vcgencmd_temp_c"] == {"samples": 3, "first": 61.0, "max": 70.5, "last": 70.5, "unknown": 0}
    assert r["api_stats_temp_c"]["max"] == 62.0


def test_capture_deltas_are_summed_per_segment_and_never_negative_from_a_reset():
    states = [state(0, 0, "P1", stereo=ok(2.0, delivered=100, depth_absent=10)),
              state(1, 3, "P1", stereo=ok(2.0, delivered=400, depth_absent=40)),
              state(2, 0, "P2", stereo=ok(2.0, delivered=5, depth_absent=0)),
              state(3, 3, "P2", stereo=ok(2.0, delivered=905, depth_absent=5))]
    c = m.summarise(rows_from(states))["capture_counter_deltas"]
    assert c["delivered"] == 300 + 900 and c["depth_absent"] == 35
    assert c["delivered_with_depth"] == 1165
    assert (c["segments_counted"], c["discontinuities"]) == (2, 1)


def test_reset_hidden_by_later_growth_is_still_a_discontinuity():
    # last > first overall, but the counter fell in between
    states = [state(0, 0, "P1", stereo=ok(2.0, delivered=100)),
              state(1, 1, "P1", stereo=ok(2.0, delivered=20)),
              state(2, 2, "P1", stereo=ok(2.0, delivered=500))]
    c = m.summarise(rows_from(states))["capture_counter_deltas"]
    assert c["discontinuities"] == 1 and c["delivered"] == 480


# ---- CPU ---------------------------------------------------------------------------------

STAT_A = "cpu  1 1 1 1 1 1 1 1\ncpu0 100 0 100 800 0 0 0 0\ncpu1 50 0 50 900 0 0 0 0\n"
STAT_B = "cpu  1 1 1 1 1 1 1 1\ncpu0 150 0 150 900 0 0 0 0\ncpu1 60 0 60 1080 0 0 0 0\n"


def test_cpu_busy_from_proc_stat_deltas():
    a, b = m.read_proc_stat(STAT_A), m.read_proc_stat(STAT_B)
    busy = m.cpu_busy(a, b)
    assert busy["cpu0"] == pytest.approx(50.0) and busy["cpu1"] == pytest.approx(10.0)
    assert "cpu" not in busy
    assert m.cpu_busy(None, b) is None                       # first sample is unknown


def test_cpu_summary_counts_unknown_intervals():
    rows = rows_from([state(i, i) for i in range(3)], cpu={"cpu0": 40.0, "cpu1": 10.0})
    rows[0]["cpu_busy_percent"] = None
    rows[2]["cpu_busy_percent"] = {"cpu0": 80.0, "cpu1": 20.0}
    c = m.summarise(rows)["local"]["cpu_busy_percent"]
    assert (c["intervals"], c["unknown_intervals"]) == (2, 1)
    assert c["per_core"]["cpu0"] == {"time_weighted_mean": 60.0, "max": 80.0}


# ---- stereo, ages, errors ----------------------------------------------------------------

def test_stereo_counts_are_labelled_as_snapshot_counts():
    states = [state(i, i, stereo=ok(2.0 + i / 10)) for i in range(5)]
    states.append(state(6, 6, stereo={"status": "unavailable", "reason": "target_coasting", "z_m": None}))
    states.append(state(7, 7))
    r = m.summarise(rows_from(states))
    assert "not per-pair" in r["stereo_snapshot_counts"]["note"]
    assert r["stereo_snapshot_counts"]["status"] == {"ok": 5, "unavailable": 1, "None": 1}
    assert r["stereo_ok_z_m"]["n"] == 5


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), True, -1.0, 0, "2.5", None])
def test_non_finite_or_non_numeric_ok_values_are_not_summarised(bad):
    r = m.summarise(rows_from([state(1, 1, stereo=ok(bad)), state(2, 2, stereo=ok(2.0))]))
    assert r["stereo_ok_z_m"] == {"n": 1, "min": 2.0, "median": 2.0, "max": 2.0}
    assert r["stereo_ok_with_invalid_value"] == 1


def test_stale_and_unknown_ages_are_counted():
    r = m.summarise(rows_from([state(1, 1, age=100), state(1, 1, age=1600), state(1, 1, age=None)]))
    assert r["state_age_ms"]["at_or_over_1500"] == 1 and r["state_age_ms"]["unknown"] == 1


def test_outage_is_visible_as_errors_and_a_gap():
    rows = rows_from([state(0, 0)])
    rows += [{"t_s": float(t), "error": "URLError"} for t in range(1, 9)]
    rows += [m.sample_from_state(state(9, 27), 9.0, {"throttled": "0x0", "vcgencmd_temp_c": 60.0})]
    r = m.summarise(rows)
    assert (r["attempts"], r["sample_errors"]) == (10, 8)
    assert r["error_kinds"] == {"URLError": 8}
    assert r["longest_gap_between_good_samples_s"] == 9.0


def test_all_errors():
    r = m.summarise([{"t_s": 0.0, "error": "x"}, {"t_s": 1.0, "error": "x"}])
    assert r["attempts"] == 2 and r["sample_errors"] == 2 and "processed_fps" not in r
    assert r["local"]["throttle"] == {"known_samples": 0, "unknown_samples": 2}


def test_lock_class_counts_expose_non_person_runs():
    r = m.summarise(rows_from([state(1, 1), state(2, 2, cls="chair"), state(3, 3, cls="chair"),
                               state(4, 4, lock=None)]))
    assert r["lock_class_snapshot_counts"] == {"person": 1, "chair": 2}


@pytest.mark.parametrize("minutes,interval", [(0, 1), (-1, 1), (float("inf"), 1), (1, 0),
                                              (float("nan"), 1), (1, float("inf"))])
def test_run_rejects_bad_durations(tmp_path, minutes, interval):
    with pytest.raises(ValueError):
        m.run("http://127.0.0.1:9", minutes, interval, tmp_path / "x", False)


# ---- review astra-0014 -------------------------------------------------------------------

def err(t, throttled="0x0", temp=60.0, cpu=None, dt=None):
    return {"t_s": float(t), "error": "URLError", "throttled": throttled, "vcgencmd_temp_c": temp,
            "cpu_busy_percent": cpu, "cpu_interval_s": dt}


def test_throttle_event_during_a_failed_request_is_not_lost():
    rows = rows_from([state(0, 0)]) + [err(1, "0x4", 85.0, {"cpu0": 100.0}, 1.0)]
    rows.append(m.sample_from_state(state(2, 6), 2.0, {"throttled": "0x0", "vcgencmd_temp_c": 61.0}))
    loc = m.summarise(rows)["local"]
    assert (loc["throttle"]["known_samples"], loc["throttle"]["unknown_samples"]) == (3, 0)
    assert loc["throttle"]["now_flags"]["throttled_now"] == {"samples": 1, "first_seen_s": 1.0}
    assert loc["throttle"]["now_flag_samples_during_failed_requests"] == 1
    assert loc["vcgencmd_temp_c"]["max"] == 85.0
    assert loc["cpu_busy_percent"]["per_core"]["cpu0"]["max"] == 100.0


def test_all_requests_failed_still_reports_local_telemetry():
    r = m.summarise([err(0), err(1, "0x80008", 84.0, {"cpu0": 90.0}, 1.0), err(2, None, None)])
    assert r["sample_errors"] == 3 and "processed_fps" not in r
    assert r["local"]["throttle"]["now_flags"]["soft_temp_limit_now"]["samples"] == 1
    assert (r["local"]["throttle"]["known_samples"], r["local"]["throttle"]["unknown_samples"]) == (2, 1)
    assert r["local"]["vcgencmd_temp_c"] == {"samples": 2, "first": 60.0, "max": 84.0, "last": 84.0, "unknown": 1}


def test_cpu_mean_is_weighted_by_interval_length():
    rows = [err(0), err(1, cpu={"cpu0": 100.0}, dt=1.0), err(4, cpu={"cpu0": 20.0}, dt=3.0)]
    c = m.summarise(rows)["local"]["cpu_busy_percent"]
    assert c["per_core"]["cpu0"]["time_weighted_mean"] == pytest.approx(40.0)
    assert c["covered_s"] == 4.0 and c["unknown_intervals"] == 1


@pytest.mark.parametrize("instance", [None, "", 5])
def test_no_rate_is_claimed_without_a_process_identity(instance):
    states = [state(15 * i, 3 * i, instance=instance) for i in range(5)]
    r = m.summarise(rows_from(states))
    assert r["processed_fps"]["value"] is None
    assert r["samples_without_process_identity"] == 5 and r["process_instances_seen"] == 0


@pytest.mark.parametrize("bad", [-1, 2.5, True, "7", None])
def test_invalid_inference_counter_values_are_not_used(bad):
    states = [state(0, 0), state(15, 3), state(30, 6)]
    states[1]["stats"]["inferences"] = bad                     # same process, one bad reading
    r = m.summarise(rows_from(states))
    assert r["segments"] == 1
    assert r["processed_fps"]["inferences_completed"] == 6     # from the two valid samples only


def owner(epoch, paired, missing, healthy=True):
    return {"epoch": epoch, "healthy": healthy, "error": None, "stereo_enabled": True,
            "limits": {"pair_wait_ms": 2000, "depth_wait_ms": 80},
            "pairing": {"paired": paired, "missing": missing}, "streams": {}}


def test_owner_diagnostics_are_summarised_independently_of_the_main_api():
    rows = [err(0), err(1), err(2), err(3)]
    rows[0]["owner"] = owner("A", 10, 0)
    rows[1]["owner"] = owner("A", 25, 1)
    rows[2]["owner_error"] = "URLError"
    rows[3]["owner"] = owner("B", 4, 0, healthy=False)
    o = m.summarise(rows)["owner"]
    assert (o["attempts"], o["errors"], o["epochs_seen"]) == (4, 1, 2)
    assert o["pairing_counter_deltas"] == {"paired": 15, "missing": 1}
    assert o["pairing_discontinuities"] == 1 and o["unhealthy_samples"] == 1
    assert o["limits_seen"] == [{"depth_wait_ms": 80, "pair_wait_ms": 2000}]


def test_no_owner_block_when_owner_was_not_sampled():
    assert "owner" not in m.summarise(rows_from([state(0, 0), state(1, 1)]))


def test_owner_sample_keeps_no_payload_fields():
    s = m.owner_sample({"epoch": "A", "jpeg": b"x", "raw": b"y", "pairing": {"paired": 1},
                        "streams": {"rgb": {"samples": 3, "capture_device_ns": 1, "stale": False}}})
    assert "jpeg" not in s and "raw" not in s
    assert s["streams"] == {"rgb": {"samples": 3, "sequence_gaps": None, "stale": False, "received_hz": None}}
