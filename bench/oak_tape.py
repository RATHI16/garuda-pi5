#!/usr/bin/env python3
"""Comparison of OAK stereo depth of a locked person against tape observations.

  oak_tape.py collect --set tune|validate --label p250 --seconds 20 --out DIR \\
        --consumer-rev <git> --producer-rev <git> \\
        [--tape-m 2.50 --tape-reference "<what was measured, from where to where>"] \\
        [--geometry-verified]
  oak_tape.py fit DIR                      (empirical scale and bias, tuning recordings only)
  oak_tape.py score DIR --set validate

One JSON file per recording. Every HTTP attempt is recorded, including failures.

What `score` will and will not do
  * A recording without a tape observation is listed as unlabelled and never compared.
  * Stale, unlocked, non-person, malformed and repeated-frame samples are excluded and counted.
    Non-finite or non-positive depth and tape values are rejected, not averaged.
  * A recording in which more than one lock id (or more than one camera epoch) was observed is
    reported as mixed and not compared: one tape reading cannot stand for several targets.
  * Three different fractions, never merged: successful requests / attempts; responses that
    carried a fresh usable ok value / attempts (both counted BEFORE removing repeats, so they
    do not change with the polling rate); and ok frames / distinct usable frames. None of
    them is time coverage.
  * One geometry and one estimator configuration per recording and per validation set:
    calibration_id and estimator_config are read from every usable sample; more than one
    value makes a recording mixed, and a validation set spanning several is refused.
  * Held-out check, and its limits: a validation comparison is refused if any validation
    recording shares a recording id, a sample-content digest or any (epoch, frame sequence)
    identity with a tuning recording, or if revisions differ within the set. That catches
    copied or relabelled data. It cannot prove two recordings are independent in any wider
    sense (same person, same minute, same lighting); that is for the session record.
  * Until the camera geometry is verified (which point and axis z_m is measured from), the
    difference from the tape is reported as `unverified_difference_m` and is NOT a depth
    accuracy figure. `z_minus_tape_m` is reported only when the recording carries
    --geometry-verified AND a geometry evidence id AND a tape uncertainty; the tape offset
    (tape observation -> depth reference) is then applied and shown. The flag is still the
    operator's assertion: this script checks that the evidence is named, not that it is right.

It states differences. It does not declare a pass.
"""
import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

SETS = ("tune", "validate")
ESTIMATOR_STATUSES = ("ok", "ambiguous", "invalid")
FRESH_MS = 1500


def num(v):
    """A finite real number that is not a bool, else None."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def pos(v):
    v = num(v)
    return v if v is not None and v > 0 else None


def frame_identity(sample):
    frame = sample.get("frame") or {}
    epoch, seq = frame.get("epoch"), frame.get("seq")
    if isinstance(epoch, str) and epoch and isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
        return epoch, seq
    return None


def classify(sample):
    """Exclusion reason for one attempt record, or None if it is usable."""
    if "error" in sample:
        return "request_failed"
    age = num(sample.get("age_ms"))
    if age is None or not 0 <= age < FRESH_MS:
        return "stale_state"
    if frame_identity(sample) is None:
        return "invalid_frame_identity"
    lock = sample.get("lock") or {}
    if lock.get("id") is None:
        return "no_lock"
    if lock.get("class") != "person":
        return "locked_class_not_person"
    if not isinstance(sample.get("stereo"), dict):
        return "no_stereo_block"
    return None


def digest(record):
    body = json.dumps(record.get("samples", []), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def frame_ids(record):
    return {fid for fid in (frame_identity(s) for s in record.get("samples", []) if "error" not in s)
            if fid is not None}


def score_recording(record):
    samples = record.get("samples", [])
    out = {"label": record.get("label"), "set": record.get("set"),
           "recording_id": record.get("recording_id"),
           "attempts": len(samples), "excluded": {}, "status": {}}
    zs, seen, locks, epochs, bad_values = [], set(), set(), set(), 0
    fresh_ok_responses, cal_ids, est_ids = 0, set(), set()
    for s in samples:
        why = classify(s)
        if why:
            out["excluded"][why] = out["excluded"].get(why, 0) + 1
            continue
        cal_ids.add(s["stereo"].get("calibration_id"))
        # the estimator ran for ok, ambiguous and invalid results; its thresholds decide those
        # rejections too, so its identity is pinned for all three, not only for successes
        if s["stereo"].get("status") in ESTIMATOR_STATUSES:
            est_ids.add(s["stereo"].get("estimator_config"))
        if s["stereo"].get("status") == "ok" and pos(s["stereo"].get("z_m")) is not None:
            fresh_ok_responses += 1                     # counted before removing repeated frames
        fid = frame_identity(s)
        if fid in seen:
            out["excluded"]["repeated_frame"] = out["excluded"].get("repeated_frame", 0) + 1
            continue
        seen.add(fid)
        locks.add(s["lock"]["id"])
        epochs.add(fid[0])
        st = s["stereo"]
        status = st.get("status")
        out["status"][str(status)] = out["status"].get(str(status), 0) + 1
        if status == "ok":
            z = pos(st.get("z_m"))
            if z is None:
                bad_values += 1
            else:
                zs.append(z)
    failed = out["excluded"].get("request_failed", 0)
    out["request_failures"] = failed
    out["successful_responses"] = len(samples) - failed
    out["distinct_usable_frames"] = len(seen)
    out["ok_frames"] = len(zs)
    out["ok_with_invalid_value"] = bad_values
    out["ok_share_of_distinct_usable_frames"] = len(zs) / len(seen) if seen else None
    n = len(samples)
    out["successful_request_share_of_attempts"] = (n - failed) / n if n else None
    out["fresh_ok_response_share_of_attempts"] = fresh_ok_responses / n if n else None
    out["new_ok_frames_per_attempt"] = len(zs) / n if n else None      # depends on polling rate
    out["lock_ids_seen"] = sorted(locks, key=str)
    out["camera_epochs_seen"] = len(epochs)
    out["calibration_ids"] = sorted(str(c) for c in cal_ids)
    out["estimator_configs"] = sorted(str(e) for e in est_ids)
    out["mixed_reasons"] = [name for name, bad in (
        ("several lock ids", len(locks) > 1), ("several camera epochs", len(epochs) > 1),
        ("several calibration ids", len(cal_ids) > 1),
        ("several estimator configurations", len(est_ids) > 1)) if bad]
    out["mixed"] = bool(out["mixed_reasons"])
    if zs:
        zs.sort()
        out["z_m"] = {"median": statistics.median(zs), "min": zs[0], "max": zs[-1],
                      "p10": zs[int(0.1 * (len(zs) - 1))], "p90": zs[int(round(0.9 * (len(zs) - 1)))]}
    tape = pos(record.get("tape_m"))
    out["tape_m"] = tape
    out["tape_reference"] = record.get("tape_reference")
    unc = num(record.get("tape_uncertainty_m"))
    off = num(record.get("tape_offset_m"))
    evidence = record.get("geometry_evidence_id")
    out["geometry_evidence_id"] = evidence
    out["tape_offset_m"], out["tape_uncertainty_m"] = off, unc
    out["geometry_verified"] = (record.get("geometry_verified") is True
                                and isinstance(evidence, str) and bool(evidence.strip())
                                and unc is not None and unc >= 0 and off is not None)
    if record.get("geometry_verified") is True and not out["geometry_verified"]:
        out["geometry_claim_incomplete"] = ("verified flag set without evidence id, tape offset "
                                            "and tape uncertainty; treated as unverified")
    if record.get("tape_m") is not None and tape is None:
        out["tape_rejected"] = "tape value is not a finite positive number"
    if tape is not None and zs and not out["mixed"]:
        # Verified: compare with the tape observation expressed in the depth reference
        # (tape + offset), and take the percentage of that same corrected reference.
        # Unverified: compare with the raw tape observation, percentage of the raw observation.
        if out["geometry_verified"]:
            reference = tape + off
            out["comparison_reference_m"] = reference
            if pos(reference) is None:
                out["comparison_rejected"] = "tape plus offset is not a finite positive distance"
            else:
                diff = out["z_m"]["median"] - reference
                out["z_minus_tape_m"] = diff
                out["z_minus_tape_percent"] = 100.0 * diff / reference
        else:
            out["comparison_reference_m"] = tape
            diff = out["z_m"]["median"] - tape
            out["unverified_difference_m"] = diff
            out["unverified_difference_percent"] = 100.0 * diff / tape
    return out


def held_out_conflicts(validate, tune):
    """Reasons the validation recordings cannot be treated as held out from the tuning ones."""
    problems = []
    t_ids = {r.get("recording_id") for r in tune}
    t_digests = {digest(r): r.get("label") for r in tune}
    t_frames = {}
    for r in tune:
        for fid in frame_ids(r):
            t_frames[fid] = r.get("label")
    for r in validate:
        if not r.get("recording_id"):
            problems.append(f"{r.get('label')}: no recording id")
        elif r["recording_id"] in t_ids:
            problems.append(f"{r.get('label')}: recording id also in the tuning set")
        d = digest(r)
        if d in t_digests:
            problems.append(f"{r.get('label')}: samples identical to tuning recording {t_digests[d]}")
        shared = [fid for fid in frame_ids(r) if fid in t_frames]
        if shared:
            problems.append(f"{r.get('label')}: {len(shared)} frame identities also in tuning "
                            f"recording {t_frames[shared[0]]}")
    ids = [r.get("recording_id") for r in validate]
    if len(set(ids)) != len(ids):
        problems.append("duplicate recording ids within the validation set")
    for field in ("consumer_rev", "producer_rev"):
        revs = {r.get(field) for r in validate}
        if None in revs or "" in revs:
            problems.append(f"{field} missing on a validation recording")
        elif len(revs) > 1:
            problems.append(f"validation recordings span several {field} values: {sorted(revs)}")
    scored = [score_recording(r) for r in validate]
    for field, what in (("calibration_ids", "calibration id"), ("estimator_configs", "estimator configuration")):
        values = {v for p in scored for v in p[field]}
        if "None" in values:
            problems.append(f"a validation sample carries no {what}")
        if len(values - {"None"}) > 1:
            problems.append(f"validation recordings span several {what}s: {sorted(values)}")
    return problems


def score(records, which):
    if which not in SETS:
        raise ValueError(f"set must be one of {SETS}")
    bad_set = [r.get("label") for r in records if r.get("set") not in SETS]
    by_set = {s: [r for r in records if r.get("set") == s] for s in SETS}
    result = {"set": which, "recordings_in_set": len(by_set[which]),
              "recordings_with_unknown_set": bad_set, "recordings": [], "unlabelled": [],
              "held_out_scope": "rejects shared recording ids, identical sample content and shared "
                                "frame identities; cannot certify wider independence"}
    if which == "validate":
        problems = held_out_conflicts(by_set["validate"], by_set["tune"])
        if problems:
            result["refused"] = problems
            return result
    for r in by_set[which]:
        p = score_recording(r)
        (result["recordings"] if p["tape_m"] is not None else result["unlabelled"]).append(p)
    result["mixed_recordings"] = [p["label"] for p in result["recordings"] if p["mixed"]]
    result["recordings_without_any_value"] = [p["label"] for p in result["recordings"] if not p["ok_frames"]]
    for key in ("z_minus_tape_m", "unverified_difference_m"):
        vals = [abs(p[key]) for p in result["recordings"] if key in p]
        if vals:
            result[f"largest_abs_{key}"] = max(vals)
            result[f"recordings_with_{key}"] = len(vals)
    if any("unverified_difference_m" in p for p in result["recordings"]):
        result["statement"] = ("geometry not verified for some recordings: their differences are "
                               "not depth accuracy")
    return result


MIN_FIT_POSITIONS = 4


def fit(records):
    """Empirical straight line  median_z = slope * tape + intercept  over the TUNING recordings.

    This describes how the stereo readings relate to the tape observations in these
    recordings. It is NOT a measurement of where the camera's depth origin is:
      * the intercept lumps together the tape's zero-mark offset, stereo bias, the choice of
        target surface and estimator bias; and if the true relation were z = a*(t + delta),
        the intercept would be a*delta, not delta;
      * the residuals are scatter about the line. They are not the tape's uncertainty and say
        nothing about systematic error or the uncertainty of the fitted slope and intercept;
      * a good fit does not show which axis z is measured along.
    So nothing here may be copied into --geometry-verified, --tape-offset-m or
    --tape-uncertainty-m as if it were established geometry. A corrected estimator has to be
    checked separately on held-out recordings.

    The result is marked exploratory unless every used recording names its calibration id,
    estimator configuration, tape reference and both revisions, and they all agree."""
    tune = [r for r in records if r.get("set") == "tune"]
    used, skipped = [], []
    for r in tune:
        p = score_recording(r)
        why = ("no tape value" if p["tape_m"] is None else "mixed: " + ", ".join(p["mixed_reasons"])
               if p["mixed"] else "no ok value" if not p["ok_frames"] else None)
        if why:
            skipped.append({"label": p["label"], "reason": why})
        else:
            used.append({"label": p["label"], "recording_id": p["recording_id"],
                         "tape_m": p["tape_m"], "median_z_m": p["z_m"]["median"],
                         "ok_frames": p["ok_frames"], "tape_reference": p["tape_reference"],
                         "calibration_ids": p["calibration_ids"], "estimator_configs": p["estimator_configs"],
                         "consumer_rev": r.get("consumer_rev"), "producer_rev": r.get("producer_rev")})
    out = {"tuning_recordings": len(tune), "used": used, "skipped": skipped,
           "interpretation": "empirical scale and bias for these recordings; not a camera-origin "
                             "measurement, not an uncertainty estimate, not geometry verification"}
    problems = []
    if len({u["tape_m"] for u in used}) < MIN_FIT_POSITIONS:
        problems.append(f"need at least {MIN_FIT_POSITIONS} distinct taped distances with a value")
    fields = (("calibration_ids", "calibration id"), ("estimator_configs", "estimator configuration"),
              ("tape_reference", "tape reference"), ("consumer_rev", "consumer revision"),
              ("producer_rev", "producer revision"))
    missing = []
    for field, what in fields:
        values = {json.dumps(u[field], sort_keys=True) for u in used}
        if len(values) > 1:
            problems.append(f"tuning recordings span several {what}s")
        for u in used:
            v = u[field]
            ids = v if isinstance(v, list) else [v]          # every identifier must be a real one
            empty = not ids or any(not isinstance(i, str) or not i.strip() or i.strip() == "None"
                                   for i in ids)
            if empty and what not in missing:
                missing.append(what)
    if problems:
        out["refused"] = problems
        return out
    out["exploratory"] = bool(missing)
    if missing:
        out["exploratory_because"] = ["missing " + m for m in missing]
    else:
        u0 = used[0]
        out["provenance"] = {k: u0[k] for k in ("calibration_ids", "estimator_configs",
                                               "tape_reference", "consumer_rev", "producer_rev")}
    xs, ys = [u["tape_m"] for u in used], [u["median_z_m"] for u in used]
    n, mx, my = len(xs), statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    for u in used:
        u["residual_m"] = u["median_z_m"] - (slope * u["tape_m"] + intercept)
    res = [u["residual_m"] for u in used]
    out.update({"empirical_slope": slope, "empirical_intercept_m": intercept,
                "residual_rms_m": math.sqrt(sum(r * r for r in res) / n),
                "largest_abs_residual_m": max(abs(r) for r in res),
                "tape_range_m": [min(xs), max(xs)], "positions_used": n,
                "note": "z = slope * tape + intercept, inside the taped range only, for this tape "
                        "reference, calibration, estimator configuration and these revisions"})
    return out


def collect(base, which, label, seconds, interval, out_dir, consumer_rev, producer_rev,
            tape_m=None, tape_reference="", geometry_verified=False, note="",
            geometry_evidence_id="", tape_offset_m=None, tape_uncertainty_m=None):
    if which not in SETS:
        raise ValueError(f"set must be one of {SETS}")
    if pos(seconds) is None or pos(interval) is None:
        raise ValueError("seconds and interval must be finite and positive")
    if tape_m is not None and pos(tape_m) is None:
        raise ValueError("tape value must be a finite positive number, or omitted")
    if tape_m is not None and not tape_reference.strip():
        raise ValueError("a tape value needs --tape-reference describing what was measured")
    if not consumer_rev or not producer_rev:
        raise ValueError("consumer and producer revisions are required")
    if tape_offset_m is not None and num(tape_offset_m) is None:
        raise ValueError("tape offset must be a finite number")
    if tape_uncertainty_m is not None and (num(tape_uncertainty_m) is None or tape_uncertainty_m < 0):
        raise ValueError("tape uncertainty must be a finite non-negative number")
    if geometry_verified and not (geometry_evidence_id.strip() and tape_offset_m is not None
                                  and tape_uncertainty_m is not None and tape_m is not None):
        raise ValueError("--geometry-verified needs --geometry-evidence-id, --tape-offset-m, "
                         "--tape-uncertainty-m and --tape-m")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{which}-{label}.json"
    if path.exists():
        raise FileExistsError(f"{path} exists; recordings are made once and not overwritten")
    samples, start = [], time.monotonic()
    while time.monotonic() - start < seconds:
        tick = time.monotonic()
        try:
            with urlopen(base + "/api/state", timeout=3) as r:
                s = json.load(r)
            row = {k: s.get(k) for k in ("age_ms", "frame", "lock", "stereo", "distance")}
        except (OSError, ValueError) as exc:
            row = {"error": type(exc).__name__}
        row["t_s"] = tick - start
        samples.append(row)
        time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    record = {"recording_id": uuid.uuid4().hex, "label": label, "set": which,
              "tape_m": tape_m, "tape_reference": tape_reference,
              "geometry_verified": bool(geometry_verified), "note": note,
              "geometry_evidence_id": geometry_evidence_id, "tape_offset_m": tape_offset_m,
              "tape_uncertainty_m": tape_uncertainty_m,
              "consumer_rev": consumer_rev, "producer_rev": producer_rev,
              "collected_unix": time.time(), "seconds": seconds, "interval_s": interval,
              "samples": samples}
    path.write_text(json.dumps(record) + "\n")
    return path, score_recording(record)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--base", default="http://127.0.0.1:8098")
    c.add_argument("--set", dest="which", required=True, choices=SETS)
    c.add_argument("--label", required=True)
    c.add_argument("--seconds", type=float, default=20)
    c.add_argument("--interval", type=float, default=0.1)
    c.add_argument("--out", required=True)
    c.add_argument("--consumer-rev", required=True)
    c.add_argument("--producer-rev", required=True)
    c.add_argument("--tape-m", type=float, default=None, help="raw tape observation; omit if not measured")
    c.add_argument("--tape-reference", default="", help="what the tape measured: from which point/plane to which")
    c.add_argument("--geometry-verified", action="store_true",
                   help="only when the depth reference frame and the tape's relation to it are established")
    c.add_argument("--geometry-evidence-id", default="",
                   help="benchlog entry / evidence directory that established the depth reference")
    c.add_argument("--tape-offset-m", type=float, default=None,
                   help="add to the tape observation to express it in the depth reference")
    c.add_argument("--tape-uncertainty-m", type=float, default=None)
    c.add_argument("--note", default="")
    s = sub.add_parser("score")
    s.add_argument("dir")
    s.add_argument("--set", dest="which", required=True, choices=SETS)
    f = sub.add_parser("fit", help="empirical scale and bias from the tuning recordings")
    f.add_argument("dir")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "fit":
            records = [json.loads(p.read_text()) for p in sorted(Path(a.dir).glob("*.json"))]
            print(json.dumps(fit(records), indent=1))
        elif a.cmd == "collect":
            path, summary = collect(a.base, a.which, a.label, a.seconds, a.interval, a.out,
                                    a.consumer_rev, a.producer_rev, a.tape_m, a.tape_reference,
                                    a.geometry_verified, a.note, a.geometry_evidence_id,
                                    a.tape_offset_m, a.tape_uncertainty_m)
            print(json.dumps({"written": str(path), "recording": summary}, indent=1))
        else:
            records = [json.loads(p.read_text()) for p in sorted(Path(a.dir).glob("*.json"))]
            print(json.dumps(score(records, a.which), indent=1))
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
