"""Detector snapshot -> fixed-camera stereo observation. Pure: no I/O, no clock, no state.

Contract `garuda.fixed-stereo-observation.1` (agreed in astra-0061, documented in
docs/FIXED_STEREO_OBSERVATION.md). One snapshot in, one observation out. The observation
either carries the selected person's torso-region point in the camera's optical frame
(metres: right, down, forward; forward is optical-axis depth, not radial distance), or
says why there is none. It never:

  * raises on malformed input (the answer is `status: unavailable`);
  * carries a value over from an earlier snapshot, or substitutes another range source;
  * changes the producer's qualification flags (`measurement_qualified`, `preview_only`);
  * adds a timestamp of its own. `camera_capture_host_ns` is the Pi's monotonic clock and
    `age_ms` is the producer's age at snapshot time; the reader adds transport and
    residence time on its own clock.

This module does not decide whether an unqualified preview may be used. It reports the
flags; the consumer's explicit simulation switch decides.
"""
import math

SCHEMA = "garuda.fixed-stereo-observation.1"
SOURCE = "fixed_stereo"
MIN_Z_M, MAX_Z_M = 0.3, 30.0          # same bounds as fixed_camera.py
MIN_CONFIDENCE = 0.5


def _num(v):
    return type(v) in (int, float) and math.isfinite(v)


def _int(v):
    return type(v) is int


def _str(v):
    return isinstance(v, str) and 0 < len(v) <= 256


def decode(snapshot):
    """Return the observation for one detector snapshot (the JSON of /api/state)."""
    out = {
        "schema": SCHEMA, "source": SOURCE,
        "source_instance": None, "frame_epoch": None, "frame_seq": None,
        "target_id": None, "target_class": None, "coasting": None, "confidence": None,
        "camera_capture_host_ns": None, "age_ms": None,
        "pair": None, "calibration_id": None, "estimator_config": None, "roi": None,
        "status": "unavailable", "reason": "adapter:malformed_snapshot", "optical_point_m": None,
        "measurement_qualified": False, "preview_only": True,
    }

    def fail(reason):
        out.update(status="unavailable", reason="adapter:" + reason, optical_point_m=None)
        return out

    s = snapshot
    if not isinstance(s, dict):
        return out
    stats, frame, lock = s.get("stats"), s.get("frame"), s.get("lock")
    if not isinstance(stats, dict) or not isinstance(frame, dict) or not isinstance(lock, dict) \
            or not isinstance(s.get("tracks"), list):
        return out

    # Identity and provenance first: reported even when there is no measurement.
    if _str(stats.get("instance")):
        out["source_instance"] = stats["instance"]
    if _str(frame.get("epoch")):
        out["frame_epoch"] = frame["epoch"]
    if _int(frame.get("seq")) and frame["seq"] >= 0:
        out["frame_seq"] = frame["seq"]
    if _int(frame.get("capture_host_ns")) and frame["capture_host_ns"] > 0:
        out["camera_capture_host_ns"] = frame["capture_host_ns"]
    if _num(s.get("age_ms")) and s["age_ms"] >= 0:
        out["age_ms"] = s["age_ms"]
    if None in (out["source_instance"], out["frame_epoch"], out["frame_seq"],
                out["camera_capture_host_ns"], out["age_ms"]):
        return fail("identity_or_time_missing")
    width, height = frame.get("width"), frame.get("height")
    if not (_int(width) and _int(height) and width > 0 and height > 0):
        return fail("image_size_invalid")

    block = s.get("stereo")
    if not isinstance(block, dict):
        return fail("no_stereo_block")
    # Qualification is copied, never upgraded. Anything but a literal boolean reads as the
    # cautious value.
    out["measurement_qualified"] = block.get("measurement_qualified") is True
    out["preview_only"] = block.get("preview_only") is not False
    if _str(block.get("estimator_config")):
        out["estimator_config"] = block["estimator_config"]

    # The selected target, as the camera reports it for this frame.
    tid = lock.get("id")
    if not _int(tid):
        return fail("no_selected_target")
    track = next((t for t in s["tracks"] if isinstance(t, dict) and t.get("id") == tid), None)
    if track is None:
        return fail("selected_target_not_in_frame")
    out["target_id"] = tid
    out["target_class"] = track.get("label") if _str(track.get("label")) else None
    out["coasting"] = bool(track.get("coasting")) or bool(lock.get("coasting"))
    out["confidence"] = track["conf"] if _num(track.get("conf")) else None
    if out["target_class"] != "person":
        return fail("selected_target_not_a_person")
    if out["coasting"]:
        return fail("selected_target_coasting")
    if out["confidence"] is None or not MIN_CONFIDENCE <= out["confidence"] <= 1:
        return fail("selected_target_confidence_low")

    # Depth must belong to exactly this image.
    pair, capture = block.get("pair"), frame.get("capture_pair")
    if not isinstance(pair, dict) or not _str(pair.get("epoch")) or not _int(pair.get("rgb_seq")):
        return fail("stereo_pair_identity_missing")
    out["pair"] = {"epoch": pair["epoch"], "rgb_seq": pair["rgb_seq"]}
    if not isinstance(capture, dict) or capture.get("epoch") != pair["epoch"] or capture.get("rgb_seq") != pair["rgb_seq"]:
        return fail("depth_not_from_this_image")

    # From here the producer's own status and reason are passed through unchanged.
    status, reason = block.get("status"), block.get("reason")
    if not _str(status) or not _str(reason):
        return fail("stereo_status_missing")
    out.update(status=status, reason=reason)
    if _str(block.get("calibration_id")):
        out["calibration_id"] = block["calibration_id"]
    roi = block.get("roi")
    if isinstance(roi, (list, tuple)) and len(roi) == 4 and all(_num(v) for v in roi):
        out["roi"] = [roi[0], roi[1], roi[2], roi[3]]
    if status != "ok":
        return out

    z = block.get("z_m")
    if not _num(z) or not MIN_Z_M < z < MAX_Z_M:
        return fail("depth_out_of_range")
    g = block.get("geometry")
    if not isinstance(g, dict) or g.get("distortion") != "none":
        return fail("geometry_missing")
    if out["calibration_id"] is None or g.get("calibration_id") != out["calibration_id"] or g.get("epoch") != pair["epoch"]:
        return fail("geometry_identity_mismatch")
    if g.get("width") != width or g.get("height") != height:
        return fail("geometry_size_mismatch")
    if not all(_num(g.get(k)) for k in ("fx", "fy", "cx", "cy")) or min(g["fx"], g["fy"]) <= 0 \
            or not 0 <= g["cx"] < width or not 0 <= g["cy"] < height:
        return fail("geometry_intrinsics_invalid")
    if out["roi"] is None:
        return fail("measurement_region_missing")
    x0, y0, x1, y1 = out["roi"]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        return fail("measurement_region_invalid")

    # Same projection as fixed_camera.py: the region's centre pixel at the measured depth.
    point = [((x0 + x1) / 2 - g["cx"]) * z / g["fx"], ((y0 + y1) / 2 - g["cy"]) * z / g["fy"], z]
    if not all(_num(v) for v in point):
        return fail("projection_not_finite")
    out["optical_point_m"] = point
    return out
