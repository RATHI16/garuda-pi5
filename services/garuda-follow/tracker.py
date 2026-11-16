"""Dependency-free IoU tracker (ByteTrack-lite: greedy IoU association).
No re-identification — a track lost past max_age is dropped, a returning object
gets a new id (honest mono limitation, see the spec's non-goals)."""
from __future__ import annotations
import sys, pathlib, time, math
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import Track


def iou(a, b) -> float:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0.0, x2 - x1), max(0.0, y2 - y1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class Tracker:
    def __init__(self, iou_threshold=0.3, max_age=15, max_unmatched_s=None, clock=time.monotonic):
        if max_unmatched_s is not None and (not math.isfinite(max_unmatched_s) or max_unmatched_s<=0):raise ValueError("invalid track time bound")
        self.max_unmatched_s=max_unmatched_s;self.clock=clock;self._seen={};self._last_time=None
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self._tracks: list[Track] = []
        self._next_id = 1

    def update(self, detections, frame_wh=(1, 1)) -> list[Track]:
        now=self.clock()
        if not math.isfinite(now):raise ValueError("invalid track clock")
        if self._last_time is not None and now<self._last_time:self._tracks=[];self._seen={}
        self._last_time=now
        if self.max_unmatched_s is not None:
            self._tracks=[t for t in self._tracks if now-self._seen.get(t.id,-math.inf)<self.max_unmatched_s]
        dets = [(d, (d["x"], d["y"], d["w"], d["h"])) for d in detections]
        used = set()
        # greedy match existing tracks to detections by best IoU
        for t in self._tracks:
            best_j, best_iou = -1, self.iou_threshold
            for j, (d, box) in enumerate(dets):
                if j in used or d["class_id"] != t.class_id:
                    continue
                v = iou(t.bbox, box)
                if v >= best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0:
                d, box = dets[best_j]
                used.add(best_j)
                t.bbox = box
                t.confidence = d["confidence"]
                t.label = d["label"]
                t.hits += 1
                t.age += 1
                t.time_since_update = 0
                self._seen[t.id]=now
            else:
                t.age += 1
                t.time_since_update += 1
        # spawn new tracks for unmatched detections
        for j, (d, box) in enumerate(dets):
            if j in used:
                continue
            self._tracks.append(Track(id=self._next_id, class_id=d["class_id"],
                                      label=d["label"], bbox=box,
                                      confidence=d["confidence"]))
            self._seen[self._next_id]=now
            self._next_id += 1
        # drop stale
        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]
        self._seen={t.id:self._seen[t.id] for t in self._tracks}
        return [t for t in self._tracks if t.time_since_update == 0]
