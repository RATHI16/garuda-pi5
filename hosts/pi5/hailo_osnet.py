"""OSNet person re-identification embeddings on the shared Hailo vdevice. Bench only.

EVAL-ONLY, not-product-cleared (see models/osnet_h10.md). One 512-d L2-normalised
embedding per person crop, from the same frame detection ran on. This module only produces
embeddings and their cost; it makes no identity decision.
"""
import time

import numpy as np

IN_W, IN_H = 128, 256
MAX_CROPS = 4


def crop_boxes(shape, boxes, max_crops=MAX_CROPS):
    """Integer crop rectangles (x0, y0, x1, y1) inside an image of `shape` (h, w, ...) for the
    first max_crops usable boxes (x, y, w, h). A box that is non-finite, or smaller than
    16x32 px after clipping to the image, gives None in its place: no embedding is better
    than one of a sliver. Pure; tested offline."""
    h, w = shape[:2]
    out = []
    for box in boxes[:max_crops]:
        try:
            x, y, bw, bh = (float(v) for v in box)
        except (TypeError, ValueError):
            out.append(None)
            continue
        if not all(np.isfinite(v) for v in (x, y, bw, bh)):
            out.append(None)
            continue
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
        out.append((x0, y0, x1, y1) if x1 - x0 >= 16 and y1 - y0 >= 32 else None)
    return out


def normalise(vector):
    """L2-normalised float32 copy, or None for a zero or non-finite vector."""
    v = np.asarray(vector, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if np.isfinite(n) and n > 0 else None


class HailoOsnet:
    def __init__(self, hef_path, vdevice, timeout_ms=2000):
        from hailo_platform import FormatType
        self.timeout_ms = timeout_ms
        self.im = vdevice.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        self.oname = self.im.output_names[0]
        self.im.output(self.oname).set_format_type(FormatType.FLOAT32)
        self.cm = self.im.configure()
        self.iname = self.im.input_names[0]
        shape = tuple(self.im.input(self.iname).shape)
        if shape[:2] != (IN_H, IN_W):
            raise ValueError(f"unexpected re-ID input shape {shape}")
        self.out_shape = tuple(self.im.output(self.oname).shape)
        self.last_timing = {}

    def embed(self, bgr, boxes):
        """Embeddings for boxes (x, y, w, h) in `bgr`; None where a box was unusable or the
        network output was. Records the cost in self.last_timing."""
        import cv2
        t0 = time.monotonic_ns()
        rects = crop_boxes(bgr.shape, boxes)
        out, run_ns = [], 0
        for rect in rects:
            if rect is None:
                out.append(None)
                continue
            x0, y0, x1, y1 = rect
            crop = cv2.cvtColor(cv2.resize(bgr[y0:y1, x0:x1], (IN_W, IN_H), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
            b = self.cm.create_bindings()
            b.input(self.iname).set_buffer(np.ascontiguousarray(crop, np.uint8))
            buf = np.zeros(self.out_shape, np.float32)
            b.output(self.oname).set_buffer(buf)
            t1 = time.monotonic_ns()
            self.cm.run([b], self.timeout_ms)
            run_ns += time.monotonic_ns() - t1
            out.append(normalise(buf))
        self.last_timing = dict(crops=sum(r is not None for r in rects), total_ns=time.monotonic_ns() - t0, run_ns=run_ns)
        return out
