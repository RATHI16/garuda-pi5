#!/usr/bin/env python3
"""Soak logger for garuda-mono with the OAK input: samples /api/state on a fixed interval.

It records what the demo reports; it does not judge it. Run it on the Pi, only while holding
the Pi lock: the sampling, the per-core CPU reads, the vcgencmd calls and the optional held
video stream are all part of the declared workload.

  oak_soak.py run --base http://127.0.0.1:8098 --minutes 30 --out DIR [--hold-stream]
  oak_soak.py summarise DIR/samples.jsonl

What the summary can and cannot say
  * Processed rate comes ONLY from the demo's completed-inference counter
    (stats.inferences), segmented by process instance (stats.instance). The frame sequence
    number is a CAPTURE counter - inference skips frames - so its advance is reported as
    capture progress and never as processed fps. Without the counter the processed rate is
    unknown.
  * Stereo status/reason figures are counts of 1 Hz snapshots, not per-pair yield. For
    acquisition yield use the owner process's own reason counters.
  * Throttle flags are decoded: "now" bits (0-3) are current conditions; bits 16-19 only
    say it happened at some point since boot. A sample whose flags could not be read is
    unknown, never clean.
  * Counter deltas are summed within a process instance. A restart starts a new segment and
    is reported; the gap across it is unknown.
"""
import argparse
import json
import math
import shutil
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.request import urlopen

NOW_BITS = {0: "undervoltage_now", 1: "frequency_capped_now", 2: "throttled_now",
            3: "soft_temp_limit_now"}
PAST_BITS = {16: "undervoltage_occurred", 17: "frequency_capping_occurred",
             18: "throttling_occurred", 19: "soft_temp_limit_occurred"}
CAPTURE_KEYS = ("delivered", "no_new_frame", "rejected", "stale", "unavailable",
                "epoch_changes", "depth_absent")


def num(v):
    """A finite real number that is not a bool, else None."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))]


def parse_throttled(text):
    """'0x50005' -> {'raw': 327685, 'now': [...], 'past': [...]}; None if unreadable."""
    try:
        raw = int(str(text), 16)
    except (TypeError, ValueError):
        return None
    return {"raw": raw,
            "now": [name for bit, name in NOW_BITS.items() if raw >> bit & 1],
            "past": [name for bit, name in PAST_BITS.items() if raw >> bit & 1]}


def cpu_busy(prev, cur):
    """Per-core busy percentage between two /proc/stat readings ({core: (busy, total)})."""
    if not prev or not cur:
        return None
    out = {}
    for core, (busy, total) in cur.items():
        if core in prev and total > prev[core][1]:
            out[core] = 100.0 * (busy - prev[core][0]) / (total - prev[core][1])
    return out or None


def read_proc_stat(text=None):
    try:
        text = Path("/proc/stat").read_text() if text is None else text
    except OSError:
        return None
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].startswith("cpu") and parts[0] != "cpu":
            vals = [int(x) for x in parts[1:9]]
            idle = vals[3] + vals[4]
            out[parts[0]] = (sum(vals) - idle, sum(vals))
    return out or None


def sample_from_state(state, t_s, vcgencmd=None, cpu=None):
    """Flatten one /api/state snapshot into a log row. Missing parts become None."""
    st = state.get("stereo") or {}
    frame = state.get("frame") or {}
    stats = state.get("stats") or {}
    cap = st.get("capture") or {}
    return {
        "t_s": t_s,
        "age_ms": state.get("age_ms"),
        "instance": stats.get("instance"), "inferences": stats.get("inferences"),
        "frame_seq": frame.get("seq"), "frame_epoch": frame.get("epoch"),
        "frame_source": frame.get("source"),
        "reported_fps": stats.get("fps"), "api_temp_c": stats.get("temp_c"),
        "tracks": len(state.get("tracks") or []),
        "lock_id": (state.get("lock") or {}).get("id"),
        "lock_class": (state.get("lock") or {}).get("class"),
        "distance_valid": (state.get("distance") or {}).get("valid"),
        "distance_est_m": (state.get("distance") or {}).get("est_m"),
        "stereo_status": st.get("status"), "stereo_reason": st.get("reason"),
        "stereo_z_m": st.get("z_m"), "stereo_coverage": st.get("coverage"),
        "stereo_support": st.get("support"),
        "capture": {k: cap.get(k) for k in CAPTURE_KEYS} if cap else None,
        "throttled": (vcgencmd or {}).get("throttled"),
        "vcgencmd_temp_c": (vcgencmd or {}).get("vcgencmd_temp_c"),
        "cpu_busy_percent": cpu,
    }


def owner_sample(diag):
    """Keep the owner fields worth logging; never image data."""
    streams = diag.get("streams") or {}
    return {"epoch": diag.get("epoch"), "healthy": diag.get("healthy"), "error": diag.get("error"),
            "limits": diag.get("limits"), "pairing": diag.get("pairing"),
            "stereo_enabled": diag.get("stereo_enabled"),
            "depth_processed": diag.get("depth_processed"), "depth_age_s": diag.get("depth_age_s"),
            "stereo_compute_ms": diag.get("stereo_compute_ms"), "map_interval_ms": diag.get("map_interval_ms"),
            "imu": diag.get("imu"), "imu_enabled": diag.get("imu_enabled"),
            "video": diag.get("video"), "video_config": diag.get("video_config"),
            "streams": {n: {k: v.get(k) for k in ("samples", "sequence_gaps", "stale", "received_hz")}
                        for n, v in streams.items() if isinstance(v, dict)}}


def count(v):
    """A non-negative integer counter value, else None."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def known_instance(v):
    return isinstance(v, str) and bool(v)


def local_summary(rows):
    """Temperature, throttle and CPU read on the machine itself. Uses EVERY attempt row,
    including those whose HTTP request failed: a throttle event during an outage is still a
    throttle event. A row without a reading counts as unknown."""
    out = {"attempt_rows": len(rows)}
    out["vcgencmd_temp_c"] = _temp_summary(r.get("vcgencmd_temp_c") for r in rows)
    out["vcgencmd_temp_c"]["unknown"] = len(rows) - out["vcgencmd_temp_c"]["samples"]

    parsed = [(r["t_s"], parse_throttled(r.get("throttled"))) for r in rows]
    known = [(t, p) for t, p in parsed if p is not None]
    thr = {"known_samples": len(known), "unknown_samples": len(parsed) - len(known)}
    if known:
        t0 = rows[0]["t_s"]
        thr["now_flags"] = {}
        for name in NOW_BITS.values():
            hits = [t for t, p in known if name in p["now"]]
            thr["now_flags"][name] = {"samples": len(hits),
                                      "first_seen_s": hits[0] - t0 if hits else None}
        thr["samples_with_any_now_flag"] = sum(bool(p["now"]) for _, p in known)
        thr["now_flag_samples_during_failed_requests"] = sum(
            1 for r, (_, p) in zip(rows, parsed) if p is not None and p["now"] and "error" in r)
        thr["past_flags_at_start"] = known[0][1]["past"]
        thr["past_flags_at_end"] = known[-1][1]["past"]
        thr["past_flags_newly_set_during_run"] = sorted(
            set(known[-1][1]["past"]) - set(known[0][1]["past"]))
        thr["raw_values_seen"] = dict(Counter(hex(p["raw"]) for _, p in known))
    out["throttle"] = thr

    cpu_rows = [(r["cpu_busy_percent"], pos_or(r.get("cpu_interval_s"), 1.0))
                for r in rows if r.get("cpu_busy_percent")]
    cores = sorted({c for row, _ in cpu_rows for c in row})
    per_core = {}
    for c in cores:
        vals = [(row[c], w) for row, w in cpu_rows if c in row]
        total = sum(w for _, w in vals)
        per_core[c] = {"time_weighted_mean": sum(v * w for v, w in vals) / total,
                       "max": max(v for v, _ in vals)}
    out["cpu_busy_percent"] = {"intervals": len(cpu_rows),
                               "unknown_intervals": len(rows) - len(cpu_rows),
                               "covered_s": sum(w for _, w in cpu_rows), "per_core": per_core}
    return out


def pos_or(v, default):
    v = num(v)
    return v if v is not None and v > 0 else default


def owner_summary(rows):
    """What the OAK owner process reported on its loopback diagnostics, sampled by this same
    logger. Independent of whether the main API answered."""
    tried = [r for r in rows if "owner" in r or "owner_error" in r]
    if not tried:
        return None
    good = [r["owner"] for r in tried if "owner" in r]
    out = {"attempts": len(tried), "errors": len(tried) - len(good),
           "error_kinds": dict(Counter(r["owner_error"] for r in tried if "owner_error" in r)),
           "epochs_seen": len({o.get("epoch") for o in good if o.get("epoch")}),
           "limits_seen": [json.loads(x) for x in sorted({json.dumps(o.get("limits"), sort_keys=True) for o in good})],
           "unhealthy_samples": sum(o.get("healthy") is False for o in good)}
    deltas, prev, breaks = Counter(), None, 0
    for o in good:
        p = o.get("pairing") or {}
        if prev is not None:
            pp = prev.get("pairing") or {}
            same = o.get("epoch") == prev.get("epoch") and all(
                count(p.get(k)) is not None and count(pp.get(k)) is not None and p[k] >= pp[k]
                for k in set(p) | set(pp))
            if same:
                for k in p:
                    deltas[k] += p[k] - pp[k]
            else:
                breaks += 1
        prev = o
    out["pairing_counter_deltas"] = dict(deltas)
    out["pairing_discontinuities"] = breaks
    out["note"] = ("producer acquisition totals within an owner epoch; consumer-side depth "
                   "omission (depth=0) is a separate matter and is not in these counters")
    return out


def _segments(rows):
    """Split rows into runs of one process instance (also split if a counter goes backwards)."""
    segs, cur = [], []
    for r in rows:
        if cur:
            p = cur[-1]
            back = (count(r["inferences"]) is not None and count(p["inferences"]) is not None
                    and r["inferences"] < p["inferences"])     # invalid values never split a segment
            cap_back = bool(r["capture"] and p["capture"] and any(
                num(r["capture"].get(k)) is not None and num(p["capture"].get(k)) is not None
                and r["capture"][k] < p["capture"][k] for k in CAPTURE_KEYS))
            # compare against the last sample that had a valid counter, so one bad value
            # in between cannot hide a real restart
            lastc = next((q for q in reversed(cur) if count(q["inferences"]) is not None), None)
            if lastc is not None and count(r["inferences"]) is not None and r["inferences"] < lastc["inferences"]:
                back = True
            if r["instance"] != p["instance"] or back or cap_back:
                segs.append(cur)
                cur = []
        cur.append(r)
    if cur:
        segs.append(cur)
    return segs


def _temp_summary(values):
    v = [x for x in (num(t) for t in values) if x is not None]
    return {"samples": len(v), "first": v[0], "max": max(v), "last": v[-1]} if v else {"samples": 0}


def summarise(rows):
    """Describe a run. Every figure is derived from the rows; nothing is a pass/fail verdict."""
    ok = [r for r in rows if "error" not in r]
    out = {"attempts": len(rows), "sample_errors": len(rows) - len(ok),
           "error_kinds": dict(Counter(r["error"] for r in rows if "error" in r))}
    if rows:
        out["run_span_s"] = rows[-1]["t_s"] - rows[0]["t_s"]
        gaps = [b["t_s"] - a["t_s"] for a, b in zip(ok, ok[1:])]
        out["longest_gap_between_good_samples_s"] = max(gaps) if gaps else None
        out["local"] = local_summary(rows)             # all attempts, API success or not
        owner = owner_summary(rows)
        if owner is not None:
            out["owner"] = owner
    if not ok:
        return out

    # ---- processed rate: completed-inference counter only --------------------------------
    segs = _segments(ok)
    out["process_instances_seen"] = len({r["instance"] for r in ok if known_instance(r["instance"])})
    out["segments"] = len(segs)
    out["samples_without_process_identity"] = sum(not known_instance(r["instance"]) for r in ok)
    done, covered = 0, 0.0
    for seg in segs:
        # a rate is only claimed for samples that name their process and carry a valid counter
        counted = [r for r in seg if known_instance(r["instance"]) and count(r["inferences"]) is not None]
        if len(counted) >= 2:
            done += counted[-1]["inferences"] - counted[0]["inferences"]
            covered += counted[-1]["t_s"] - counted[0]["t_s"]
    if covered > 0:
        out["processed_fps"] = {"value": done / covered, "inferences_completed": done,
                                "covered_s": covered, "uncovered_s": out["run_span_s"] - covered,
                                "source": "stats.inferences deltas within a process instance"}
    else:
        out["processed_fps"] = {"value": None,
                                "source": "unknown: no valid stats.inferences with a stats.instance"}

    # ---- capture progress (NOT processed fps) --------------------------------------------
    adv, epochs, prev = 0, set(), None
    for r in ok:
        if num(r["frame_seq"]) is None or r["frame_epoch"] is None:
            continue
        epochs.add(r["frame_epoch"])
        if prev is not None and prev[0] == r["frame_epoch"] and r["frame_seq"] > prev[1]:
            adv += r["frame_seq"] - prev[1]
        prev = (r["frame_epoch"], r["frame_seq"])
    out["capture_sequence"] = {"advance": adv, "frame_epochs_seen": len(epochs),
                               "note": "capture counter; inference skips frames"}
    out["distinct_frames_observed_in_snapshots"] = len(
        {(r["frame_epoch"], r["frame_seq"]) for r in ok if num(r["frame_seq"]) is not None})

    ages = [a for a in (num(r["age_ms"]) for r in ok) if a is not None]
    out["state_age_ms"] = {"samples": len(ages), "unknown": len(ok) - len(ages),
                           "median": pct(ages, .5), "p95": pct(ages, .95),
                           "max": max(ages) if ages else None,
                           "at_or_over_1500": sum(a >= 1500 for a in ages)}
    fps = [f for f in (num(r["reported_fps"]) for r in ok) if f is not None]
    out["reported_fps_field"] = {"median": pct(fps, .5), "min": min(fps) if fps else None,
                                 "note": "the demo's own 2 s window figure, for reference"}

    # API-reported temperature (successful responses only; local readings are under "local")
    out["api_stats_temp_c"] = _temp_summary(r["api_temp_c"] for r in ok)

    # ---- stereo: snapshot counts ---------------------------------------------------------
    out["stereo_snapshot_counts"] = {
        "note": "counts of 1 Hz state snapshots, not per-pair acquisition yield",
        "status": dict(Counter(str(r["stereo_status"]) for r in ok)),
        "reason": dict(Counter(str(r["stereo_reason"]) for r in ok))}
    out["lock_class_snapshot_counts"] = dict(
        Counter(str(r["lock_class"]) for r in ok if r["lock_id"] is not None))
    z = [v for v in (num(r["stereo_z_m"]) for r in ok if r["stereo_status"] == "ok")
         if v is not None and v > 0]
    out["stereo_ok_z_m"] = ({"n": len(z), "min": min(z), "median": statistics.median(z),
                             "max": max(z)} if z else {"n": 0})
    out["stereo_ok_with_invalid_value"] = sum(
        1 for r in ok if r["stereo_status"] == "ok" and (num(r["stereo_z_m"]) is None or r["stereo_z_m"] <= 0))

    # ---- capture counters, per segment ---------------------------------------------------
    deltas, counted_segments = Counter(), 0
    for seg in segs:
        caps = [r["capture"] for r in seg
                if r["capture"] and all(num(r["capture"].get(k)) is not None for k in CAPTURE_KEYS)]
        if len(caps) >= 2:
            counted_segments += 1
            for k in CAPTURE_KEYS:
                deltas[k] += caps[-1][k] - caps[0][k]
    if counted_segments:
        out["capture_counter_deltas"] = {
            **{k: deltas[k] for k in CAPTURE_KEYS},
            "segments_counted": counted_segments,
            "discontinuities": len(segs) - 1,
            "delivered_with_depth": deltas["delivered"] - deltas["depth_absent"],
            "note": "summed within each process instance; intervals across a restart are unknown; "
                    "depth is requested only while a target is locked"}
    else:
        out["capture_counter_deltas"] = None
    return out


def read_vcgencmd():
    if not shutil.which("vcgencmd"):
        return None

    def run(arg):
        try:
            return subprocess.run(["vcgencmd", arg], capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    thr, temp = run("get_throttled"), run("measure_temp")
    out = {"throttled": thr.split("=", 1)[1] if thr and "=" in thr else None}
    try:
        out["vcgencmd_temp_c"] = float(temp.split("=", 1)[1].rstrip("'C")) if temp else None
    except (ValueError, IndexError):
        out["vcgencmd_temp_c"] = None
    return out


def hold_stream(base, stop):
    """Keep one MJPEG viewer open and discard the bytes, so inference keeps running."""
    while not stop.is_set():
        try:
            with urlopen(base + "/stream", timeout=5) as r:
                while not stop.is_set() and r.read(65536):
                    pass
        except OSError:
            stop.wait(1)


def run(base, minutes, interval, out_dir, hold, owner_base=None, on_sample=None):
    if not (num(minutes) and minutes > 0 and num(interval) and interval > 0):
        raise ValueError("minutes and interval must be finite and positive")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    stop = threading.Event()
    if hold:
        threading.Thread(target=hold_stream, args=(base, stop), daemon=True).start()
    rows, start, prev_cpu, prev_cpu_t = [], time.monotonic(), None, None
    with open(out / "samples.jsonl", "w") as fh:
        while time.monotonic() - start < minutes * 60:
            tick = time.monotonic()
            cur_cpu = read_proc_stat()
            cpu = cpu_busy(prev_cpu, cur_cpu)          # first sample: unknown
            cpu_dt = tick - prev_cpu_t if prev_cpu_t is not None else None
            prev_cpu, prev_cpu_t = cur_cpu, tick
            vc = read_vcgencmd()
            try:
                with urlopen(base + "/api/state", timeout=3) as r:
                    row = sample_from_state(json.load(r), tick - start, vc, cpu)
            except (OSError, ValueError) as exc:
                row = {"t_s": tick - start, "error": type(exc).__name__,
                       "throttled": (vc or {}).get("throttled"),
                       "vcgencmd_temp_c": (vc or {}).get("vcgencmd_temp_c"),
                       "cpu_busy_percent": cpu}
            row["cpu_interval_s"] = cpu_dt if cpu else None
            if owner_base:                              # same logger, same declared workload
                try:
                    with urlopen(owner_base + "/diagnostics", timeout=3) as r:
                        row["owner"] = owner_sample(json.load(r))
                except (OSError, ValueError) as exc:
                    row["owner_error"] = type(exc).__name__
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if on_sample is not None:
                on_sample(row)
            time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    stop.set()
    summary = summarise(rows)
    summary["config"] = {"base": base, "minutes": minutes, "interval_s": interval,
                         "held_stream": hold, "owner_base": owner_base}
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", default="http://127.0.0.1:8098")
    r.add_argument("--minutes", type=float, required=True)
    r.add_argument("--interval", type=float, default=1.0)
    r.add_argument("--out", required=True)
    r.add_argument("--hold-stream", action="store_true")
    r.add_argument("--owner-base", default=None,
                   help="e.g. http://127.0.0.1:8096 - also sample the OAK owner's /diagnostics")
    s = sub.add_parser("summarise")
    s.add_argument("samples")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        result = run(a.base, a.minutes, a.interval, a.out, a.hold_stream, a.owner_base)
    else:
        result = summarise([json.loads(line) for line in open(a.samples) if line.strip()])
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
