"""Identity recorder for the detector: one JSON line per processed frame, numbers only.

For choosing and checking re-identification rules offline. Never stores pixels. A recording
is started on request, is bounded in time and size, and stops by itself. No hailo or cv2."""
import json
import re
import time
from pathlib import Path

MAX_SECONDS = 300
MAX_FRAMES = 6000
LABEL = re.compile(r"^[a-z0-9-]{1,40}$")


def frame_record(seq, capture_t, lock_id, pinned, tracks_full, stereo, embeddings, histograms):
    """Pure. tracks_full rows are (id, label, x, y, w, h, conf, coasting, ...)."""
    def r(v, n=4):
        return None if v is None else round(float(v), n)
    st = stereo or {}
    return {
        "seq": int(seq), "t": r(capture_t, 6), "lock": lock_id, "pinned": bool(pinned),
        "tracks": [{"id": int(t[0]), "label": t[1], "box": [r(v, 1) for v in t[2:6]], "conf": r(t[6], 3),
                    "coasting": bool(t[7]) if len(t) > 7 else False} for t in tracks_full],
        "stereo": {k: st.get(k) for k in ("status", "reason", "z_m", "roi", "coverage", "support")} if stereo is not None else None,
        "emb": {str(k): [r(x) for x in v] for k, v in (embeddings or {}).items()},
        "hist": {str(k): [r(x) for x in v] for k, v in (histograms or {}).items()},
    }


def onboard_record(seq, capture_t, onboard, box, stereo, temp_c):
    """Pure. One line of a carry-around recording: the command that would have been given, the
    selected box it came from and the stereo result. Numbers and short reasons only."""
    def r(v, n=4):
        return None if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v else round(float(v), n)
    o, st = onboard or {}, stereo or {}
    return {"seq": int(seq), "t": r(capture_t, 6), "valid": bool(o.get("valid")), "reason": o.get("reason"),
            "yaw_rate": r(o.get("yaw_rate")), "forward_vel": r(o.get("forward_vel")),
            "forward_reason": o.get("forward_reason"), "range_m": r(o.get("range_m"), 3),
            "range_source": o.get("range_source"), "target": o.get("target_id"),
            "box": [r(v, 1) for v in box] if box is not None else None,
            "stereo": {"status": st.get("status"), "reason": st.get("reason")} if stereo is not None else None,
            "temp_c": r(temp_c, 1)}


class Recorder:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.file = None
        self.until = 0.0
        self.frames = 0
        self.path = None

    def start(self, label, seconds, now=None):
        """Returns the file path. Raises ValueError for a bad label or duration, or while a
        recording is already running."""
        now = time.monotonic() if now is None else now
        if not isinstance(label, str) or not LABEL.match(label):
            raise ValueError("label must be 1-40 characters of a-z, 0-9 or -")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 1 <= seconds <= MAX_SECONDS:
            raise ValueError(f"seconds must be 1..{MAX_SECONDS}")
        if self.active(now):
            raise ValueError("a recording is already running")
        self.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}.jsonl"
        self.file = self.path.open("x")
        self.until, self.frames = now + seconds, 0
        return str(self.path)

    def active(self, now=None):
        now = time.monotonic() if now is None else now
        return self.file is not None and now < self.until and self.frames < MAX_FRAMES

    def write(self, record, now=None):
        """Append one frame while active; closes itself when the time or frame limit is reached."""
        if not self.active(now):
            self.close()
            return False
        self.file.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
        self.frames += 1
        return True

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
