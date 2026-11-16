"""Stereo depth of one selected person: aligned depth image + box (+ keypoints) -> z_m.

Pure function, numpy only. No device, no I/O. Input depth follows docs/oak-coordination.md:
uint16 millimetres, 0 = invalid, aligned to the RGB image the box and keypoints refer to.

What it returns is optical-axis depth ("z_m", stereo depth). It is NOT range and NOT horizontal
standoff: that needs the verified depth reference frame plus intrinsics/extrinsics, and is
deliberately absent here.

Method
  1. Region: the torso. From shoulder and hip keypoints when all four are confident, shrunk
     inwards; otherwise the upper-central part of the box.
  2. Valid pixels: depth > 0. `coverage` = valid pixels / region pixels.
  3. Group valid depths into clusters separated by a gap in depth.
  4. Select a cluster only if it dominates the valid pixels. Never "the nearest": a hand, an
     object or another person can be nearer than the selected torso. Two substantial clusters
     without a dominant one -> "ambiguous", no number.
  5. The selected cluster must also be compact in depth; a wide smear is not a surface.
  6. z_m = median of the selected cluster. `support` = its share of the valid pixels.

`coverage` and `support` are separate quality scores. Neither is a probability.

All thresholds in Params are provisional design values chosen for synthetic tests; none has
been validated against measured distances.
"""
from dataclasses import dataclass

import numpy as np

L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 5, 6, 11, 12


@dataclass(frozen=True)
class Params:
    kpt_conf: float = 0.5          # all four torso keypoints must reach this to be used
    kpt_shrink: float = 0.10       # shrink the keypoint torso rectangle inwards by this fraction
    box_x: tuple = (0.30, 0.70)    # box-only torso: horizontal span as fractions of box width
    box_y: tuple = (0.20, 0.55)    #                 vertical span as fractions of box height
    min_roi_px: int = 400          # smaller regions give no result
    min_coverage: float = 0.20     # valid pixels / region pixels
    gap_abs_mm: float = 200.0      # clusters split where neighbouring sorted depths differ by
    gap_rel: float = 0.08          #   more than max(gap_abs_mm, gap_rel * depth)
    substantial: float = 0.15      # a cluster with at least this share of valid pixels competes
    dominance: float = 0.60        # the selected cluster needs at least this share
    spread_abs_mm: float = 400.0   # selected cluster's 10th-90th percentile span must not exceed
    spread_rel: float = 0.15       #   max(spread_abs_mm, spread_rel * its median)


def params_id(p=None):
    """Short stable identity of the estimator thresholds, so recordings made with different
    settings are never pooled."""
    import hashlib
    return hashlib.sha256(repr(p or Params()).encode()).hexdigest()[:12]


def _finite(*values):
    try:
        return all(np.isfinite(float(v)) for v in values)
    except (TypeError, ValueError):
        return False


def _clip_roi(x0, y0, x1, y1, w, h):
    x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
    x1, y1 = min(w, int(round(x1))), min(h, int(round(y1)))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def torso_roi(box, kpts, shape, p=Params()):
    """Return ((x0, y0, x1, y1) or None, source) with source 'keypoints' or 'box'."""
    h, w = shape
    try:
        bx, by, bw, bh = box
    except (TypeError, ValueError):
        return None, "box"
    if not _finite(bx, by, bw, bh) or bw <= 0 or bh <= 0:
        return None, "box"
    try:
        pts = [kpts[i] for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)] if len(kpts) == 17 else None
        usable = pts is not None and all(len(k) >= 3 and _finite(*k[:3]) for k in pts)
    except (TypeError, IndexError, KeyError):
        pts, usable = None, False
    if usable:
        if all(k[2] >= p.kpt_conf for k in pts):
            xs, ys = [k[0] for k in pts], [k[1] for k in pts]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            dx, dy = (x1 - x0) * p.kpt_shrink, (y1 - y0) * p.kpt_shrink
            roi = _clip_roi(max(x0 + dx, bx), max(y0 + dy, by),
                            min(x1 - dx, bx + bw), min(y1 - dy, by + bh), w, h)
            if roi is not None:
                return roi, "keypoints"
    roi = _clip_roi(bx + p.box_x[0] * bw, by + p.box_y[0] * bh,
                    bx + p.box_x[1] * bw, by + p.box_y[1] * bh, w, h)
    return roi, "box"


def depth_clusters(valid_mm, p=Params()):
    """Split sorted valid depths at gaps.

    Returns [(count, median_mm, p10_to_p90_span_mm)] in depth order. Gap splitting chains
    through evenly scattered depths, so a cluster can be wide; callers must check the span."""
    v = np.sort(valid_mm.astype(np.float64))
    if v.size == 0:
        return []
    gaps = np.diff(v)
    cut = np.nonzero(gaps > np.maximum(p.gap_abs_mm, p.gap_rel * v[:-1]))[0] + 1
    return [(int(part.size), float(np.median(part)),
             float(np.percentile(part, 90) - np.percentile(part, 10)))
            for part in np.split(v, cut)]


def _result(status, reason, roi, source, coverage=0.0, support=0.0, z_m=None, candidates=None):
    out = {"status": status, "reason": reason, "z_m": z_m,
           "coverage": float(coverage), "support": float(support),
           "roi": roi, "roi_source": source}
    if candidates is not None:
        out["candidates"] = candidates
    return out


def estimate_person_z(depth_mm, box, kpts=None, params=None):
    """depth_mm: HxW uint16 mm (0 invalid). box: (x, y, w, h) px, top-left origin.
    kpts: optional 17 x [x, y, conf] in the same pixel space.

    Returns a dict: status 'ok' | 'ambiguous' | 'invalid', reason, z_m (float or None),
    coverage, support, roi (x0, y0, x1, y1) or None, roi_source, and for 'ambiguous' a
    'candidates' list of {z_m, support}.

    Malformed or non-finite boxes give status 'invalid' (reason 'bad_box'); malformed or
    non-finite keypoints are ignored and the box region is used. Neither raises, so bad pose
    metadata cannot stop detection. Known limit: if an occluder covers most of the torso
    region, the dominant cluster is the occluder."""
    p = params or Params()
    if not isinstance(depth_mm, np.ndarray) or depth_mm.ndim != 2 or depth_mm.dtype != np.uint16:
        raise ValueError("depth_mm must be a 2-D uint16 array of millimetres")

    roi, source = torso_roi(box, kpts, depth_mm.shape, p)
    if roi is None:
        try:
            bad = not (_finite(*box) and len(box) == 4 and box[2] > 0 and box[3] > 0)
        except TypeError:
            bad = True
        return _result("invalid", "bad_box" if bad else "roi_too_small", None, source)
    x0, y0, x1, y1 = roi
    return estimate_region_z(depth_mm[y0:y1, x0:x1], roi, source, p)


def reduce_nearest(patch, k=2):
    """k x k blocks -> the nearest valid depth in each block (0 where the block is empty).
    Host stereo is matched on a grid k times coarser than the colour image and projected
    without hole filling, so at colour resolution most pixels are empty by construction;
    this puts the patch back on the grid it was measured on."""
    h, w = patch.shape
    H, W = -(-h // k) * k, -(-w // k) * k
    full = np.full((H, W), 65535, np.uint16)
    full[:h, :w] = np.where(patch > 0, patch, 65535)
    out = full.reshape(H // k, k, W // k, k).min(axis=(1, 3))
    out[out == 65535] = 0
    return out


def estimate_region_z(patch, roi, source, params=None):
    """The estimator's decision for the pixels of one region. `patch` holds that region
    (possibly reduced); `roi` and `source` are reported back unchanged."""
    p = params or Params()
    if patch.size < p.min_roi_px:
        return _result("invalid", "roi_too_small", roi, source)

    valid = patch[patch > 0]
    coverage = valid.size / patch.size
    if valid.size == 0:
        return _result("invalid", "no_valid_depth", roi, source)
    if coverage < p.min_coverage:
        return _result("invalid", "insufficient_valid_depth", roi, source, coverage)

    clusters = sorted(depth_clusters(valid, p), reverse=True)        # largest first
    shares = [(n / valid.size, med) for n, med, _ in clusters]
    top_share, top_mm = shares[0]
    if top_share >= p.dominance:
        if clusters[0][2] > max(p.spread_abs_mm, p.spread_rel * top_mm):
            return _result("invalid", "cluster_too_spread", roi, source, coverage, top_share)
        return _result("ok", "ok", roi, source, coverage, top_share, z_m=top_mm / 1000.0)

    rivals = [(s, mm) for s, mm in shares if s >= p.substantial]
    if len(rivals) >= 2:
        return _result("ambiguous", "competing_depth_clusters", roi, source, coverage, top_share,
                       candidates=[{"z_m": mm / 1000.0, "support": float(s)} for s, mm in rivals])
    return _result("invalid", "no_dominant_cluster", roi, source, coverage, top_share)
