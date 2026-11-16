#!/usr/bin/env python3
"""Offline description of IMU sequence gaps in an OAK first-light sample file.

Reads the samples.jsonl(.gz) written by hosts/pi5/oak/first_light.py. Never opens a device.

It DESCRIBES the data; it does not decide whether samples were lost. A sequence-counter step
larger than 1 can mean "the sensor counts faster than it is asked to deliver" or "a sample was
dropped", and the counter alone cannot tell those apart. So the output keeps separate:

  regular steps    - counter steps that recur often (>= 5 % of intervals). A category, not a
                     verdict: recurring loss would look the same.
  irregular steps  - rarer, larger counter jumps.
  long intervals   - intervals whose device-timestamp spacing exceeds 1.5x the requested
                     period, judged from timestamps only, independent of the counter.
  rates            - delivered rate and counter rate against the requested rate. A delivered
                     rate well below the requested one is evidence of loss; a counter rate
                     well above it is evidence the counter is not a delivered-sample index.
  near-coincident  - irregular steps in accel and gyro starting within 10 ms of each other.
                     Suggests a shared cause; does not prove a whole batch was lost.

Repeated rows are removed and counted; counter resets or backwards timestamps split the
stream into segments that are analysed separately.

Usage: oak_imu_analysis.py SAMPLES [--requested-hz 400 | --accel-hz 500 --gyro-hz 400]
                           [--out result.json]
"""
import argparse
import gzip
import json
import statistics
import sys
from collections import Counter

REGULAR_FRACTION = 0.05
LONG_INTERVAL_FACTOR = 1.5


def load_streams(path, names=("accel", "gyro")):
    opener = gzip.open if str(path).endswith(".gz") else open
    out = {n: [] for n in names}
    with opener(path, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("stream") in out:
                out[rec["stream"]].append(rec)
    return out


def clean(samples):
    """Drop repeated reports and split at resets.

    Returns (segments, counts). A row is a duplicate when it repeats the previous kept row's
    counter value and device timestamp. A new segment starts when the counter or the device
    timestamp goes backwards, or the counter repeats with a different timestamp."""
    counts = {"raw_rows": len(samples), "duplicates": 0, "segment_breaks": 0}
    segments, cur = [], []
    for s in samples:
        if cur:
            prev = cur[-1]
            if s["seq"] == prev["seq"] and s["device_s"] == prev["device_s"]:
                counts["duplicates"] += 1
                continue
            if s["seq"] <= prev["seq"] or s["device_s"] <= prev["device_s"]:
                counts["segment_breaks"] += 1
                segments.append(cur)
                cur = []
        cur.append(s)
    if cur:
        segments.append(cur)
    counts["unique_rows"] = sum(len(seg) for seg in segments)
    return segments, counts


def _span(samples):
    return samples[-1]["device_s"] - samples[0]["device_s"]


def analyse_segment(samples, requested_hz=None):
    pairs = list(zip(samples, samples[1:]))
    steps = Counter(b["seq"] - a["seq"] for a, b in pairs)
    total = sum(steps.values())
    regular = sorted(s for s, n in steps.items() if n >= REGULAR_FRACTION * total)
    limit = max(regular)
    span = _span(samples)

    by_step = {}
    for s in regular:
        dts = [(b["device_s"] - a["device_s"]) * 1e3 for a, b in pairs if b["seq"] - a["seq"] == s]
        by_step[str(s)] = {"intervals": len(dts), "median_dt_ms": statistics.median(dts)}

    irregular = []
    for a, b in pairs:
        step = b["seq"] - a["seq"]
        if step > limit:
            age = None
            if "arrival_monotonic_s" in b and "host_synced_s" in b:
                age = (b["arrival_monotonic_s"] - b["host_synced_s"]) * 1e3
            irregular.append({"t_device_s": a["device_s"], "seq_step": step,
                              "interval_ms": (b["device_s"] - a["device_s"]) * 1e3,
                              "arrival_age_ms": age})

    out = {
        "rows": len(samples),
        "span_s": span,
        "delivered_rate_hz": (len(samples) - 1) / span if span > 0 else None,
        "counter_rate_hz": (samples[-1]["seq"] - samples[0]["seq"]) / span if span > 0 else None,
        "requested_rate_hz": requested_hz,
        "raw_sequence_gaps": sum((s - 1) * n for s, n in steps.items() if s > 1),
        "regular_steps": regular,
        "regular_step_intervals": by_step,
        "gaps_in_regular_steps": sum((s - 1) * steps[s] for s in regular if s > 1),
        "irregular_step_events": len(irregular),
        "gaps_in_irregular_steps": sum(e["seq_step"] - 1 for e in irregular),
        "irregular_interval_span_ms_total": sum(e["interval_ms"] for e in irregular),
        "longest_irregular_interval_ms": max((e["interval_ms"] for e in irregular), default=0.0),
        "first_irregular_s_from_start": irregular[0]["t_device_s"] - samples[0]["device_s"] if irregular else None,
        "last_irregular_s_from_start": irregular[-1]["t_device_s"] - samples[0]["device_s"] if irregular else None,
        "irregular_events": irregular,
    }
    if requested_hz:
        period_ms = 1e3 / requested_hz
        long_dts = [(b["device_s"] - a["device_s"]) * 1e3 for a, b in pairs
                    if (b["device_s"] - a["device_s"]) * 1e3 > LONG_INTERVAL_FACTOR * period_ms]
        out["long_intervals"] = {
            "threshold_ms": LONG_INTERVAL_FACTOR * period_ms,
            "count": len(long_dts),
            "excess_over_requested_period_ms_total": sum(d - period_ms for d in long_dts),
        }
        out["delivered_over_requested"] = out["delivered_rate_hz"] / requested_hz
        out["counter_over_requested"] = out["counter_rate_hz"] / requested_hz
    return out


def analyse_stream(samples, requested_hz=None):
    segments, counts = clean(samples)
    usable = [seg for seg in segments if len(seg) > 2]
    return {"counts": counts,
            "segments": [analyse_segment(seg, requested_hz) for seg in usable]}


def near_coincident(events_a, events_b, window_s=0.01):
    """Events in A that have an event in B starting within window_s."""
    times_b = [e["t_device_s"] for e in events_b]
    return [e for e in events_a if any(abs(e["t_device_s"] - t) <= window_s for t in times_b)]


def analyse(streams, requested_hz=None):
    """requested_hz: one rate for every sensor, or {sensor: rate} when they were requested
    at different rates (a sensor missing from the dict gets no rate-based figures)."""
    def rate(name):
        return requested_hz.get(name) if isinstance(requested_hz, dict) else requested_hz
    result = {name: analyse_stream(s, rate(name)) for name, s in streams.items() if len(s) > 2}
    if "accel" in result and "gyro" in result:
        breaks = {n: result[n]["counts"]["segment_breaks"] for n in ("accel", "gyro")}
        if any(breaks.values()):
            # after a reset, device timestamps from different runs can overlap, so matching
            # events on time across segments could invent coincidences
            result["near_coincident"] = {"suppressed": "a stream contains a reset; device "
                                         "timestamps are not comparable across it",
                                         "segment_breaks": breaks}
            return result
        ev = {n: [e for seg in result[n]["segments"] for e in seg["irregular_events"]]
              for n in ("accel", "gyro")}
        result["near_coincident"] = {
            "window_s": 0.01,
            "gyro_irregular_with_accel_irregular_nearby": len(near_coincident(ev["gyro"], ev["accel"])),
            "gyro_irregular_total": len(ev["gyro"]),
            "accel_irregular_total": len(ev["accel"]),
        }
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("samples")
    ap.add_argument("--requested-hz", type=float, default=None,
                    help="rate requested from the device, if the same for both sensors")
    ap.add_argument("--accel-hz", type=float, default=None, help="accelerometer requested rate")
    ap.add_argument("--gyro-hz", type=float, default=None, help="gyroscope requested rate")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.requested_hz is not None and (args.accel_hz is not None or args.gyro_hz is not None):
        ap.error("give either --requested-hz or the per-sensor rates, not both")
    requested = args.requested_hz
    if args.accel_hz is not None or args.gyro_hz is not None:
        requested = {"accel": args.accel_hz, "gyro": args.gyro_hz}
    result = analyse(load_streams(args.samples), requested)
    result["requested_hz"] = requested
    result["source"] = args.samples
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(json.dumps(result, indent=1) + "\n")

    def brief(node):
        if isinstance(node, dict):
            return {k: brief(v) for k, v in node.items() if k != "irregular_events"}
        if isinstance(node, list):
            return [brief(v) for v in node]
        return node
    print(json.dumps(brief(result), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
