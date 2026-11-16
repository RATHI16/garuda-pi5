"""Unit tests for the offline IMU gap description. Synthetic data only; no device."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import oak_imu_analysis as m  # noqa: E402


def stream(seqs, period_s):
    """Samples whose timestamps follow the counter exactly (counter period = period_s)."""
    return [{"seq": s, "device_s": s * period_s, "host_synced_s": s * period_s,
             "arrival_monotonic_s": s * period_s + 0.01} for s in seqs]


def only(result):
    assert len(result["segments"]) == 1
    return result["segments"][0]


def test_clean_stream():
    r = only(m.analyse_stream(stream(range(1000), 0.0025), requested_hz=400))
    assert r["raw_sequence_gaps"] == 0
    assert r["irregular_step_events"] == 0
    assert r["long_intervals"]["count"] == 0
    assert round(r["delivered_over_requested"], 2) == 1.0
    assert round(r["counter_over_requested"], 2) == 1.0


def test_fast_counter_shows_as_counter_above_requested_not_as_shortfall():
    # counter at 500 Hz, every fifth count not delivered -> 400 Hz delivered
    seqs = [s for s in range(5000) if s % 5 != 4]
    r = only(m.analyse_stream(stream(seqs, 0.002), requested_hz=400))
    assert r["regular_steps"] == [1, 2]
    assert r["gaps_in_regular_steps"] == r["raw_sequence_gaps"] > 0
    assert round(r["counter_over_requested"], 2) == 1.25
    assert round(r["delivered_over_requested"], 2) == 1.0
    assert r["long_intervals"]["count"] > 0          # 4 ms intervals at a 2.5 ms request


def test_recurring_loss_is_not_hidden_by_the_regular_step_category():
    # Review finding R1: counter at the requested 400 Hz, every tenth sample missing.
    seqs = [s for s in range(1000) if s % 10 != 9]
    r = only(m.analyse_stream(stream(seqs, 0.0025), requested_hz=400))
    assert r["regular_steps"] == [1, 2]              # same category as a fast counter...
    assert r["gaps_in_regular_steps"] == 99
    assert round(r["counter_over_requested"], 2) == 1.0   # ...but the counter is not fast,
    assert r["delivered_over_requested"] < 0.91           # delivery falls short,
    assert r["long_intervals"]["count"] == 99             # and timestamps show every loss
    assert abs(r["long_intervals"]["excess_over_requested_period_ms_total"] - 99 * 2.5) < 1e-6


def test_irregular_jump_reported_apart_from_regular_steps():
    seqs = [s for s in range(5000) if s % 5 != 4 and not 2000 <= s < 2012]
    r = only(m.analyse_stream(stream(seqs, 0.002), requested_hz=400))
    assert r["irregular_step_events"] == 1
    ev = r["irregular_events"][0]
    assert ev["seq_step"] >= 12
    assert abs(ev["interval_ms"] - ev["seq_step"] * 2.0) < 1e-6
    assert r["irregular_interval_span_ms_total"] == ev["interval_ms"]


def test_duplicate_rows_are_counted_and_do_not_inflate_rate():
    base = stream(range(1000), 0.0025)
    doubled = [row for s in base for row in (s, dict(s))]
    res = m.analyse_stream(doubled, requested_hz=400)
    assert res["counts"] == {"raw_rows": 2000, "duplicates": 1000,
                             "segment_breaks": 0, "unique_rows": 1000}
    r = only(res)
    assert round(r["delivered_over_requested"], 2) == 1.0
    assert r["regular_steps"] == [1]


def test_counter_reset_splits_into_segments():
    first = stream(range(500), 0.0025)
    second = [{**s, "device_s": s["device_s"] + 2.0} for s in stream(range(400), 0.0025)]
    res = m.analyse_stream(first + second, requested_hz=400)
    assert res["counts"]["segment_breaks"] == 1
    assert [seg["rows"] for seg in res["segments"]] == [500, 400]
    assert all(seg["raw_sequence_gaps"] == 0 for seg in res["segments"])


def test_backwards_timestamp_splits_into_segments():
    a = stream(range(300), 0.0025)
    b = [{**s, "seq": s["seq"] + 300, "device_s": s["device_s"]} for s in stream(range(300), 0.0025)]
    res = m.analyse_stream(a + b)
    assert res["counts"]["segment_breaks"] == 1
    assert len(res["segments"]) == 2


def test_near_coincident_matches_on_time_only():
    a = [{"t_device_s": 1.000}, {"t_device_s": 5.000}]
    b = [{"t_device_s": 1.004}, {"t_device_s": 9.000}]
    assert m.near_coincident(a, b) == [{"t_device_s": 1.000}]


def test_analyse_reports_near_coincident_counts():
    gyro = stream([s for s in range(4000) if not 1000 <= s < 1008], 0.0025)
    accel = stream([s for s in range(4000) if not 1000 <= s < 1008], 0.0025)
    r = m.analyse({"accel": accel, "gyro": gyro})
    assert r["near_coincident"]["gyro_irregular_with_accel_irregular_nearby"] == 1
    assert r["near_coincident"]["gyro_irregular_total"] == 1
    assert r["near_coincident"]["accel_irregular_total"] == 1


# ---- follow-ups from review astra-0003 ---------------------------------------------------

def test_per_sensor_requested_rates():
    accel = stream(range(5000), 0.002)                 # 500 Hz
    gyro = stream(range(4000), 0.0025)                 # 400 Hz
    r = m.analyse({"accel": accel, "gyro": gyro}, {"accel": 500, "gyro": 400})
    assert round(only(r["accel"])["delivered_over_requested"], 2) == 1.0
    assert round(only(r["gyro"])["delivered_over_requested"], 2) == 1.0
    assert only(r["accel"])["long_intervals"]["count"] == 0
    one = m.analyse({"accel": accel, "gyro": gyro}, 400)   # the old single-rate reading is wrong for accel
    assert round(only(one["accel"])["delivered_over_requested"], 2) == 1.25


def test_sensor_without_a_requested_rate_gets_no_rate_figures():
    r = m.analyse({"accel": stream(range(1000), 0.002), "gyro": stream(range(1000), 0.0025)}, {"gyro": 400})
    assert "delivered_over_requested" not in only(r["accel"])
    assert "delivered_over_requested" in only(r["gyro"])


def test_near_coincidence_is_suppressed_when_a_stream_contains_a_reset():
    first = stream([s for s in range(2000) if not 500 <= s < 508], 0.0025)
    second = stream([s for s in range(2000) if not 500 <= s < 508], 0.0025)   # restarted device clock
    gyro = first + second
    accel = stream([s for s in range(4000) if not 500 <= s < 508], 0.0025)
    r = m.analyse({"accel": accel, "gyro": gyro})
    assert "suppressed" in r["near_coincident"]
    assert r["near_coincident"]["segment_breaks"] == {"accel": 0, "gyro": 1}
    assert "gyro_irregular_with_accel_irregular_nearby" not in r["near_coincident"]
