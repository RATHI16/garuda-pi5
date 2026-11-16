"""Pure builders for garuda-mono's wire state (v2). No hailo/cv2 imports — unit-tested on x86 CI.

The snapshot is the single JSON object served by GET /api/state and streamed by
GET /api/events; services/garuda-portal/mono_client.py consumes exactly these keys.
v2 adds optional per-track `kpts` (pose keypoints) and a top-level `gesture` field.
"""
import math


def fresh_stereo(block, age_ms, limit_ms=1500):
    """The stereo block as it may be shown: a block older than the freshness limit (or of
    unknown age) keeps its identity fields but loses its value, so a frozen 'ok' is never
    displayed while the camera service is down."""
    if block is None:
        return None
    if age_ms is not None and 0 <= age_ms < limit_ms:
        return block
    stale = dict(block)
    stale.update(status="unavailable", reason="stale_state", z_m=None, coverage=None,
                 support=None, roi=None, roi_source=None)
    stale.pop("candidates", None)
    return stale


MOUNTS = ("fixed", "vehicle")


def mount_setting(value):
    """(mount, locked). The camera mount is a physical fact: when the service environment states
    it, it is locked and the page cannot change it; unset means a bench that may switch."""
    if value in MOUNTS:
        return value, True
    return "fixed", False


def onboard_obs(tracks_full, lock_id, coasting, frame_w, frame_h, age_ms, identity, caps, stereo, pinned=False):
    """Detector rows -> the observation `onboard_command.command` takes. The distance source is
    fixed by the camera, never by what happens to be available in a frame: the OAK uses its
    stereo distance or nothing; a one-lens camera uses box height (a size proxy)."""
    # Only a person an operator explicitly selected is ever followed: the detector's automatic
    # "best person in view" lock (class mode) is never a target for a vehicle command.
    # Only a PERSON is followed: the page and API can select other objects, which never qualify.
    row = next((t for t in tracks_full if pinned and lock_id is not None and t[0] == lock_id and t[1] == "person"), None)
    camera = (caps or {}).get("camera")
    source = ("stereo" if (caps or {}).get("distance_source") == "stereo"
              else "box_height" if camera in ("cm3", "usb") else "none")
    target = None
    if row is not None:
        target = {"id": int(row[0]), "x": row[2], "y": row[3], "w": row[4], "h": row[5],
                  "conf": float(row[6]) if len(row) > 6 else 0.0,
                  "coasting": bool(coasting) or (bool(row[7]) if len(row) > 7 else False)}
    ident = None
    if identity is not None:
        ident = bool(identity.get("confirmed")) and identity.get("state") == "tracking" and identity.get("target") == lock_id
    return {"frame_w": frame_w, "frame_h": frame_h, "age_ms": age_ms, "target": target, "identity": ident,
            "range_source": source, "stereo": stereo if source == "stereo" else None}


def fresh_onboard(block, age_ms, limit_ms):
    """The published command must not outlive its frame: once the frame is older than the
    command's own age limit, readers get a HOLD instead of the last speeds."""
    if block is None:
        return None
    out = dict(block, transmit=False)
    if not isinstance(age_ms, (int, float)) or age_ms > limit_ms:
        out.update(valid=False, reason="stale frame", yaw_rate=0.0, forward_vel=0.0, vertical_vel=0.0,
                   forward_reason="hold", range_m=None)
    return out


def capabilities(source, stereo_enabled, mono_depth_enabled, mono_engine=None, mount="fixed"):
    """What the camera feeding this detector can honestly offer, for pages and consumers to
    word and gate themselves by. `distance_source` is the ONE kind of person distance this
    process publishes: "stereo" (OAK pair, host-matched), "mono_estimate" (a depth network's
    estimate from one image; never a measurement) or "none". Stereo is never reported for a
    camera that has one lens, and nothing here falls back from one kind to the other."""
    camera = ("oak" if source == "oak-rgb" else "cm3" if source == "csi"
              else "usb" if isinstance(source, str) and source.startswith("/dev/") else "unknown")
    stereo = bool(stereo_enabled) and camera == "oak"
    return {"camera": camera,
            "distance_source": "stereo" if stereo else "mono_estimate" if mono_depth_enabled else "none",
            "mono_engine": mono_engine if (mono_depth_enabled and not stereo) else None,
            "depth_map": stereo, "cloud": stereo,
            "mount": mount if mount in MOUNTS else "fixed"}


def positive_range(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and 0 < value <= 30


def range_valid(raw, est, engine, age, coasting=False):
    raw_ok = isinstance(raw, (int, float)) and math.isfinite(raw) and raw > 0
    if engine != "da":
        raw_ok = raw_ok and positive_range(raw)
    return bool(raw_ok and age is not None and 0 <= age < 1500 and not coasting
                and (positive_range(est) if est is not None else engine != "da"))


def select_track(alive, lid, wanted, pinned, reacq):
    locked = next((t for t in alive if t.track_id == lid), None)
    if pinned:
        return locked  # Never substitute another person for an explicit selection.
    if locked is None and wanted:
        candidates = [t for t in alive if t.cls == wanted]
        return max(candidates, key=lambda t: (t.time_since_update == 0, t.conf), default=None)
    if locked is not None and locked.time_since_update > reacq and wanted:
        live = [t for t in alive if t.cls == wanted and t.time_since_update == 0]
        return max(live, key=lambda t: t.conf, default=locked)
    return locked


def build_snapshot(state: dict, extras: dict) -> dict:
    raw = state.get("raw")
    est = extras.get("est_m")
    valid = bool(extras.get("valid", False)) and range_valid(
        raw, est, extras.get("depth_engine"), extras.get("age_ms"), extras.get("lock_coasting", False))
    # An invalid fitted value must not silently fall back to an uncalibrated value.
    if est is not None and not positive_range(est):
        valid = False
    metric_raw = raw if extras.get("depth_engine") != "da" else None
    tracks = []
    for t in state.get("tracks_full", []):
        tid, label, x, y, w, h = t[:6]
        conf = float(t[6]) if len(t) > 6 else 0.0
        coasting = bool(t[7]) if len(t) > 7 else False
        tracks.append({"id": int(tid), "label": label, "conf": conf,
                       "x": float(x), "y": float(y), "w": float(w), "h": float(h),
                       "coasting": coasting})
        if len(t) > 8 and t[8] is not None:
            tracks[-1]["kpts"] = t[8]
        appearance = state.get("appearances", {}).get(tid)
        if appearance is not None:
            tracks[-1]["appearance"] = appearance
        if tid in state.get("faces", {}):
            tracks[-1]["face"] = state["faces"][tid]
        if tid in state.get("track_gestures", {}):
            tracks[-1]["gesture"] = state["track_gestures"][tid]
    return {
        "v": 2,
        "age_ms": extras.get("age_ms"),
        "tracks": tracks,
        "lock": {"id": extras.get("lock_id"), "class": state.get("class", ""),
                 "coasting": bool(extras.get("lock_coasting", False))},
        "distance": {"raw_m": metric_raw if valid else None, "est_m": est if valid else None,
                     "valid": valid and (est is not None or metric_raw is not None)},
        "depth_engine": extras.get("depth_engine", ""),
        "detector": {"mode": extras.get("detector_mode", "primary"),
                     "prompt": list(extras.get("prompt", []))},
        "cal": {"n": int(extras.get("cal_n", 0)), "fit": extras.get("fit")},
        "frame": {"capture_host_ns": int(state["t"] * 1e9) if state.get("t") is not None else None,
                  "width": int(state.get("frame_w", extras.get("frame_w", 0))),
                  "height": int(state.get("frame_h", extras.get("frame_h", 0))),
                  **({"capture_pair": {"epoch": state["pair_key"][0], "rgb_seq": state["pair_key"][1]}} if state.get("pair_key") is not None else {}),
                  **({"seq": state["frame_seq"], "source": state.get("frame_source"),
                      "epoch": state.get("frame_epoch")} if "frame_seq" in state else {})},
        "gesture": extras.get("gesture") or {"name": None, "track_id": None, "held_ms": 0},
        "stats": extras.get("stats") or {},
        "face_service": extras.get("face_service") or {},
        # Optional, additive: OAK stereo depth of the locked target for this exact frame
        # (oak_stereo_state.stereo_measurement). Separate from `distance`, which it does not
        # replace or feed until stereo has been validated against measured distances.
        # It obeys the same freshness limit as `distance` (see range_valid).
        **({"stereo": fresh_stereo(state["stereo"], extras.get("age_ms"))}
           if state.get("stereo") is not None else {}),
        # Optional, additive: camera-on-vehicle shadow command for this frame (never transmitted
        # by this process). Present only while the mount is "vehicle".
        **({"onboard": fresh_onboard(state["onboard"], extras.get("age_ms"), extras.get("onboard_max_age_ms", 400))}
           if state.get("onboard") is not None else {}),
        # Optional, additive: where the operator asked that command to be sent, and what the
        # sender there reports (mono_transmit). This process itself sends nothing.
        **({"transmit": extras["transmit"]} if isinstance(extras.get("transmit"), dict) else {}),
    }


def _iou_xywh(ax, ay, aw, ah, bx, by, bw, bh):
    """IoU of two (x, y, w, h) boxes in the same pixel convention."""
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


SELECT_MAX_AGE_S = 0.7      # same bound the display uses for "fresh" observations


def select_by_id(state: dict, instance: str, query: dict, now: float, max_age_s: float = SELECT_MAX_AGE_S):
    """Exact-ID selection check against one published state. Pure; the caller holds the
    state lock and applies the result, so check and selection are one atomic step.
    query is parse_qs output. Returns (True, selection) or (False, {"ok": False, "reason"}).
    Nothing is selected unless the detector process, the frame epoch and a visible,
    non-coasting track with that ID all match what the requester saw, AND the published
    observation is fresh: `now` (same clock as state["t"], seconds) must be at most
    max_age_s after the frame's capture. The published state keeps its last tracks when
    capture stops, so without this a track could be pinned long after the camera did."""
    def one(name):
        v = query.get(name)
        return v[0] if isinstance(v, list) and len(v) == 1 else None
    def refuse(reason):
        return False, {"ok": False, "reason": reason}
    if set(query) - {"id", "epoch", "instance"}:
        return refuse("unknown_parameter")
    raw, epoch, wanted = one("id"), one("epoch"), one("instance")
    if raw is None or epoch is None or wanted is None or not raw.isdigit() or len(raw) > 12:
        return refuse("id_epoch_instance_required")
    if wanted != instance:
        return refuse("detector_restarted")
    if "frame_seq" not in state or epoch != state.get("frame_epoch"):
        return refuse("frame_epoch_changed")
    t = state.get("t")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not isinstance(now, (int, float)) \
            or not math.isfinite(t) or not math.isfinite(now) or t <= 0 or now - t < 0:
        return refuse("observation_time_invalid")
    if now - t > max_age_s:
        return refuse("observation_stale")
    tid = int(raw)
    track = next((t for t in state.get("tracks_full", []) if int(t[0]) == tid), None)
    if track is None:
        return refuse("track_not_present")
    if len(track) > 7 and track[7]:
        return refuse("track_coasting")
    return True, {"ok": True, "id": tid, "label": track[1], "epoch": epoch, "instance": instance,
                  "seq": state["frame_seq"]}


def prune_unmatched(tracks, last_matched: dict, now_s: float, max_unmatched_s: float):
    """Tracks that may still be matched on the frame captured at now_s (seconds).

    A track whose last matched frame is more than max_unmatched_s older than this frame is
    dropped, so a detection that appears afterwards starts a new track ID instead of
    inheriting the old one. Frame counts cannot express this: during a stall few frames pass
    while much time does. A backwards or non-finite clock drops every remembered track.
    Tracks never seen by note_matched (just created) are kept. last_matched is pruned too."""
    ok = isinstance(now_s, (int, float)) and not isinstance(now_s, bool) and math.isfinite(now_s)
    kept = []
    for t in tracks:
        seen = last_matched.get(t.track_id)
        if seen is None or (ok and 0 <= now_s - seen <= max_unmatched_s):
            kept.append(t)
    alive = {t.track_id for t in kept}
    for tid in [tid for tid in last_matched if tid not in alive]:
        del last_matched[tid]
    return kept


def note_matched(tracks, last_matched: dict, now_s: float):
    """Record the capture time for every track matched on this frame."""
    for t in tracks:
        if t.time_since_update == 0:
            last_matched[t.track_id] = now_s


def attach_kpts(tracks_full, dets):
    """Match pose-mode dets (duck-typed `.x .y .w .h .kpts`) onto tracker output by IoU.

    tracks_full: list of 8-tuples (tid, label, x, y, w, h, conf, coasting) — the tracker's own
    output, unchanged. dets: this frame's raw detections (HailoYoloPose's Dets carry `.kpts`;
    non-pose detectors' Detections don't have the attribute at all, so getattr guards them out).
    Best IoU > 0.5 wins per track; ties/no-match leave the track as the original 8-tuple (kpts
    absent -> the v2 builder above simply doesn't add the "kpts" key)."""
    kpt_dets = [d for d in dets if getattr(d, "kpts", None) is not None]
    out = []
    for t in tracks_full:
        tid, label, x, y, w, h = t[:6]
        best_iou, best_kpts = 0.5, None
        for d in kpt_dets:
            i = _iou_xywh(x, y, w, h, d.x, d.y, d.w, d.h)
            if i > best_iou:
                best_iou, best_kpts = i, d.kpts
        out.append(t + (best_kpts,) if best_kpts is not None else t)
    return out
