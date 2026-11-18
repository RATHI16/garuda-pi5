"""Pure glue between the /pair consumer and garuda-mono's state. No hailo, cv2 or sockets.

Rule enforced here: a frame's stereo depth is looked up by the identity of the frame that
inference actually processed — (epoch, RGB sequence). There is no "latest depth" fallback.
A frame without its own depth has no stereo measurement.
"""
from collections import OrderedDict

from oak_distance import estimate_person_z, params_id


def frame_key(sensor, frame_id):
    """Key for an OAK RGB frame from FrameMeta.sensor ('oak-rgb:<epoch>') and frame_id."""
    if not isinstance(sensor, str) or not sensor.startswith("oak-rgb:"):
        return None
    return sensor.split(":", 1)[1], int(frame_id)


class PairDepthStore:
    """Bounded map (epoch, rgb_seq) -> (depth_mm or None, pair metadata)."""

    CLOUD_STEPS = (2, 4, 8)  # grids the 3-D view may ask for

    def __init__(self, capacity=4):
        self.capacity = capacity
        self._items = OrderedDict()
        self._thumbs = {}

    def put(self, pair):
        key = (pair.meta["epoch"], pair.meta["rgb"]["seq"])
        self._items[key] = (pair.depth_mm, pair.meta)
        self._items.move_to_end(key)
        if pair.depth_mm is not None:
            # Keep a reference to the pair's own RGB bytes (no copy, no per-frame work);
            # the 3-D view slices it only when someone asks.
            rgb = pair.meta["rgb"]
            self._thumbs[key] = (pair.rgb, rgb["height"], rgb["width"])
        while len(self._items) > self.capacity:
            old, _ = self._items.popitem(last=False)
            self._thumbs.pop(old, None)
        return key

    def thumb(self, key, step=8):
        """Every step-th pixel of the pair's own RGB image as BGR (None if the pair had no
        depth). step must be one of CLOUD_STEPS."""
        if step not in self.CLOUD_STEPS:
            raise ValueError(f"step must be one of {self.CLOUD_STEPS}")
        rgb = self.rgb(key)
        return None if rgb is None else rgb[::step, ::step]

    def rgb(self, key):
        """The exact pair’s full colour raster, backed by its retained bytes."""
        t = self._thumbs.get(key)
        if t is None:
            return None
        import numpy as np
        data, h, w = t
        return np.frombuffer(data, dtype=np.uint8).reshape(h, w, 3)

    def get(self, key):
        return self._items.get(key)

    def latest_with_depth(self):
        """(key, depth_mm, meta) of the newest stored pair that carries depth, or None.
        For the depth VIEW only; measurements always use the processed frame's own key."""
        for key in reversed(self._items):
            depth_mm, meta = self._items[key]
            if depth_mm is not None:
                return key, depth_mm, meta
        return None

    def clear(self):
        self._items.clear()
        self._thumbs.clear()

    def __len__(self):
        return len(self._items)


def _block(status, reason, key=None, meta=None, result=None):
    out = {"status": status, "reason": reason, "z_m": None, "coverage": None, "support": None,
           "roi": None, "roi_source": None, "pair": None,
           "depth_reference_frame": None, "calibration_id": None, "pair_skew_ns": None, "geometry": None, "measurement_qualified": False, "preview_only": False}
    if key is not None:
        out["pair"] = {"epoch": key[0], "rgb_seq": key[1]}
    if meta is not None:
        out["pair"]["pair_id"] = meta["pair_id"]
        out["depth_reference_frame"] = meta["depth_reference_frame"]
        out["calibration_id"] = meta.get("calibration_id")
        out["geometry"] = meta.get("geometry")
        out["measurement_qualified"] = meta.get("measurement_qualified") is True
        out["preview_only"] = meta.get("preview_only", False)
        out["pair_skew_ns"] = meta["pair_skew_ns"]
    if result is not None:
        out.update({k: result[k] for k in ("z_m", "coverage", "support", "roi", "roi_source")})
        if "candidates" in result:
            out["candidates"] = result["candidates"]
    return out


SUPPORTED_CLASSES = ("person",)      # the estimator's region is a human torso; nothing else


def stereo_measurement(store, key, box, kpts=None, coasting=False, params=None,
                       target_class="person"):
    """Stereo depth of the locked target for exactly the frame identified by key.

    status: 'ok' | 'ambiguous' | 'invalid' (from the estimator) or 'unavailable' (no
    measurement attempted). z_m is optical-axis stereo depth; never range or standoff."""
    return measure_entry(store.get(key) if key is not None else None, key, box, kpts,
                         coasting, params, target_class)


def measure_entry(entry, key, box, kpts=None, coasting=False, params=None,
                  target_class="person"):
    """As stereo_measurement, for an entry already taken from the store (so the caller can
    hold its lock only for the lookup, not for the computation)."""
    if key is None:
        return _block("unavailable", "not_an_oak_pair_frame")
    if entry is None:
        return _block("unavailable", "no_pair_for_frame", key)
    depth_mm, meta = entry
    if meta.get("measurement_qualified") is False:
        return _block("unavailable", "unqualified_depth", key, meta)
    if depth_mm is None:
        return _block("unavailable", meta["depth_reason"], key, meta)
    if box is None:
        return _block("unavailable", "no_target", key, meta)
    if target_class not in SUPPORTED_CLASSES:
        # Torso-region logic applied to a chair or a bottle would be a number with no meaning.
        return _block("unavailable", "unsupported_target_class", key, meta)
    if coasting:
        # A coasting box is a prediction, not an observation of where the person is.
        return _block("unavailable", "target_coasting", key, meta)
    result = estimate_person_z(depth_mm, box, kpts, params)
    block = _block(result["status"], result["reason"], key, meta, result)
    block["estimator_config"] = params_id(params)      # only where the estimator actually ran
    return block


class EpochGate:
    """Detects a capture-epoch change from the frames actually processed.

    The reset decision is derived from the epoch of the frame about to be processed versus
    the last one processed, never from a one-shot flag set by another thread (a flag can be
    read and cleared on an iteration that then skips, losing the event)."""

    def __init__(self):
        self.epoch = None

    def entering(self, frame_epoch):
        """Call once per frame that WILL be processed. True -> discard all tracking, lock,
        gesture and cached-depth state before using this frame."""
        changed = self.epoch is not None and frame_epoch != self.epoch
        self.epoch = frame_epoch
        return changed

    @staticmethod
    def may_publish(processed_epoch, current_epoch):
        """False when a newer epoch's frame has already been captured: results computed on
        the old run's frame must not be published as current state."""
        return processed_epoch == current_epoch



VIEW_STALE_NS = 1_500_000_000        # same freshness limit as the distance and stereo blocks


def view_selection(store, stereo_block, now_ns, stale_ns=VIEW_STALE_NS):
    """What the depth VIEW may show: (key, depth_mm, roi_or_None), or None when nothing fresh.

    * Prefers the pair the published stereo block was measured on, if it is still stored and
      has depth: then the outline and the depth are from the same pair.
    * Otherwise the newest stored pair with depth, and NO outline - an outline from another
      pair would be an approximate overlay, which is exactly what must not be shown.
    * Depth whose RGB capture time (host monotonic clock) is older than stale_ns, or in the
      future, is not shown at all, so a frozen picture cannot pass for a live one.
    """
    def fresh(meta):
        t = meta["rgb"]["capture_host_ns"]
        return 0 <= now_ns - t < stale_ns

    pair = (stereo_block or {}).get("pair") or {}
    key = (pair.get("epoch"), pair.get("rgb_seq"))
    entry = store.get(key) if None not in key else None
    if entry is not None and entry[0] is not None and fresh(entry[1]):
        return key, entry[0], (stereo_block or {}).get("roi")
    latest = store.latest_with_depth()
    if latest is not None and fresh(latest[2]):
        return latest[0], latest[1], None
    return None
