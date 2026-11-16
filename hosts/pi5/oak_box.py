"""Stereo depth of one person from the owner's /box-depth route (host stereo, box only).

The owner matches just the strip of an already-captured mono pair that can contain the
requested colour-image region. Here that region is the estimator's torso region for the
locked person in the frame detection has just processed, so the number belongs to exactly
that frame. Unvalidated preview: blocks carry measurement_qualified False, preview_only True.
No hailo or cv2 imports."""
import json
import struct
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import numpy as np

LEFT_RIGHT_NS = 5_000_000
RGB_SPAN_NS = 33_333_333
SCHEMA = "garuda.oak-box-depth.1"


def read_box_depth(stream, content_length, region):
    """Read one /box-depth answer for region (x0, y0, x1, y1). Returns (meta, uint16 mm patch).
    Raises ValueError on any breach. Timing limits are checked here, not taken from the sender."""
    def need(ok, msg):
        if not ok:
            raise ValueError(msg)

    def exact(n, what):
        data = stream.read(n)
        need(len(data) == n, "truncated " + what)
        return data

    (n,) = struct.unpack(">I", exact(4, "length prefix"))
    need(0 < n <= 16384, "metadata length out of range")
    try:
        meta = json.loads(exact(n, "metadata").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("metadata is not valid UTF-8 JSON") from None
    need(isinstance(meta, dict) and meta.get("schema") == SCHEMA, "not a box-depth answer")
    need(meta.get("preview_only") is True and meta.get("measurement_qualified") is False
         and meta.get("depth_backend") == "pi-sgbm-box", "box depth must be an unqualified CPU stereo preview")
    x0, y0, x1, y1 = region
    w, h = x1 - x0, y1 - y0
    need(meta.get("roi") == [x0, y0, x1, y1] and (meta.get("width"), meta.get("height")) == (w, h)
         and meta.get("format") == "Z16LE", "answer is for another region")
    need(meta.get("length") == w * h * 2 and content_length == 4 + n + w * h * 2, "bad patch length")
    a, b = meta.get("left_right_skew_ns"), meta.get("rgb_span_ns")
    need(type(a) is int and 0 <= a <= LEFT_RIGHT_NS, "left and right frames too far apart")
    need(type(b) is int and 0 <= b <= RGB_SPAN_NS, "colour and stereo frames too far apart")
    raw = exact(w * h * 2, "patch")
    need(stream.read(1) == b"", "trailing bytes")
    return meta, np.frombuffer(raw, dtype="<u2").reshape(h, w).astype(np.uint16, copy=False)


def refusal_reason(error):
    """Reason string from a /box-depth refusal body, or 'http_<code>'."""
    try:
        body = error.read()
        (n,) = struct.unpack(">I", body[:4])
        return str(json.loads(body[4:4 + n])["reason"])
    except Exception:
        return "http_" + str(getattr(error, "code", "error"))


class BoxStereo:
    """One measurement per processed frame for the locked person. Never raises for a missing
    or refused measurement: the block says why there is no number."""

    GRID = 2        # host stereo is matched at half the colour image's width and height

    def __init__(self, base="http://127.0.0.1:8096", opener=urlopen, shape=(720, 1280), timeout=0.25):
        self.base, self.opener, self.timeout, self.shape = base, opener, timeout, shape
        self.counters = {"requests": 0, "readings": 0, "refused": 0, "errors": 0, "last_ms": None}

    def measure(self, key, capture_ns, box, kpts=None, coasting=False, target_class="person", params=None):
        """key: (owner epoch, RGB sequence) of the processed frame; capture_ns: its host
        capture time. Returns a stereo block in the same shape oak_stereo_state produces."""
        from oak_distance import Params, estimate_person_z, estimate_region_z, params_id, reduce_nearest, torso_roi
        from oak_stereo_state import SUPPORTED_CLASSES, _block
        if key is None or capture_ns is None:
            return _block("unavailable", "not_an_oak_pair_frame")
        if box is None:
            return _block("unavailable", "no_target", key)
        if target_class not in SUPPORTED_CLASSES:
            return _block("unavailable", "unsupported_target_class", key)
        if coasting:
            return _block("unavailable", "target_coasting", key)
        roi, source = torso_roi(box, kpts, self.shape, params or Params())
        if roi is None:                                # the estimator names the reason (bad box, too small)
            result = estimate_person_z(np.zeros(self.shape, np.uint16), box, kpts, params)
            return _block(result["status"], result["reason"], key, None, result)
        x0, y0, x1, y1 = (int(v) for v in roi)
        started = time.monotonic()
        self.counters["requests"] += 1
        try:
            url = f"{self.base}/box-depth?epoch={key[0]}&rgb_host_ns={int(capture_ns)}&x0={x0}&y0={y0}&x1={x1}&y1={y1}"
            with self.opener(url, timeout=self.timeout) as r:
                meta, patch = read_box_depth(r, int(r.headers["Content-Length"]), (x0, y0, x1, y1))
        except HTTPError as e:
            self.counters["refused"] += 1
            # 404: the owner is running without host stereo (capture only); say so plainly.
            return _block("unavailable", "stereo_unavailable" if e.code == 404 else refusal_reason(e), key)
        except (OSError, ValueError, KeyError, TypeError):
            self.counters["errors"] += 1
            return _block("unavailable", "stereo_unavailable", key)
        if meta.get("epoch") != key[0] or (meta.get("rgb") or {}).get("seq") != key[1]:
            self.counters["errors"] += 1
            return _block("unavailable", "no_pair_for_frame", key)
        # Same decision rules as the estimator, applied on the grid the depth was measured on:
        # coverage and the minimum region size are in stereo pixels, not colour pixels.
        result = estimate_region_z(reduce_nearest(patch, self.GRID), (x0, y0, x1, y1), source, params)
        block = _block(result["status"], result["reason"], key, dict(
            pair_id=key[1], depth_reference_frame=meta.get("depth_reference_frame", "unverified"),
            calibration_id=meta.get("calibration_id"), geometry=meta.get("geometry"),
            measurement_qualified=False, preview_only=True, pair_skew_ns=meta["rgb_span_ns"]), result)
        block["estimator_config"] = params_id(params)
        block["source"] = "host-box-stereo"
        block["coverage_grid"] = self.GRID
        self.counters["last_ms"] = (time.monotonic() - started) * 1000
        if block["status"] == "ok":
            self.counters["readings"] += 1
        return block
