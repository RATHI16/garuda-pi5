"""OakPairBackend against fake HTTP responses. No sockets, no device."""
import io
import time
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import numpy as np
import pytest

import oak_capture
import oak_pair as op

W, H = 1280, 720
RGB = bytes(W * H * 3)


class Response(io.BytesIO):
    def __init__(self, body=b"", status=200, content_type=op.CONTENT_TYPE, length=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": content_type,
                        "Content-Length": str(len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def body(depth=True, age_ns=0, **kw):
    now = time.monotonic_ns() - age_ns
    d = np.full((H, W), 2500, dtype=np.uint16) if depth else None
    return op.encode_pair(RGB, d, rgb_device_ns=1_000, depth_device_ns=1_000,
                          host_offset_ns=now - 1_000, **kw)[0]


class Harness:
    def __init__(self, want_depth=True):
        self.frames, self.pairs, self.epochs, self.urls = [], [], [], []
        self.backend = oak_capture.OakPairBackend(
            on_pair=self.pairs.append, on_epoch_change=self.epochs.append,
            want_depth=lambda: want_depth)
        self.backend.configure(W, H, 15)
        self.cursor = op.PairCursor()

    def poll(self, response):
        def fake(url, timeout=None):
            self.urls.append(url)
            if isinstance(response, Exception):
                raise response
            return response
        with patch.object(oak_capture, "urlopen", side_effect=fake):
            return self.backend.poll_once(self.cursor, lambda m, px: self.frames.append((m, len(px))))


def test_pair_with_depth_is_delivered_to_both_callbacks():
    h = Harness()
    assert h.poll(Response(body(pair_id=3, epoch="E1", rgb_seq=41))) == "delivered"
    meta, size = h.frames[0]
    assert (meta.frame_id, meta.sensor, meta.clock_domain, size) == (41, "oak-rgb:E1", "mono", len(RGB))
    assert h.pairs[0].depth_mm.shape == (H, W)
    assert h.urls == ["http://127.0.0.1:8096/pair?depth=1"]


def test_rgb_only_still_delivers_the_frame():
    h = Harness()
    assert h.poll(Response(body(depth=False, reason="pair_wait_expired"))) == "delivered"
    assert len(h.frames) == 1 and h.pairs[0].depth_mm is None
    assert h.backend.counters["depth_absent"] == 1


def test_cursor_is_sent_on_the_next_request_and_depth_can_be_declined():
    h = Harness(want_depth=False)
    h.poll(Response(body(depth=False, reason="omitted_by_request", pair_id=3, epoch="E1")))
    h.poll(Response(status=204))
    assert h.urls[1] == "http://127.0.0.1:8096/pair?after=3&epoch=E1&depth=0"


def test_204_delivers_nothing():
    h = Harness()
    assert h.poll(Response(status=204)) == "no_new_frame"
    assert h.frames == [] and h.pairs == []


@pytest.mark.parametrize("exc,outcome", [
    (HTTPError("u", 503, "x", {}, None), "unavailable"),
    (HTTPError("u", 400, "x", {}, None), "rejected"),
    (URLError("refused"), "unavailable"),
    (ConnectionResetError(), "unavailable"),
])
def test_errors_deliver_nothing(exc, outcome):
    h = Harness()
    assert h.poll(exc) == outcome
    assert h.frames == []


def test_stale_frame_is_not_delivered():
    h = Harness()
    assert h.poll(Response(body(age_ns=2_000_000_000))) == "stale"
    assert h.frames == [] and h.cursor.epoch is None


def test_timestamp_from_the_future_is_not_delivered():
    h = Harness()
    assert h.poll(Response(body(age_ns=-5_000_000_000))) == "stale"


def test_contract_breach_is_not_delivered():
    h = Harness()
    b = body()
    assert h.poll(Response(b[:-10], length=len(b))) == "rejected"            # truncated
    assert h.poll(Response(b, content_type="application/octet-stream")) == "rejected"
    assert h.poll(Response(b, length=len(b) + 4)) == "rejected"
    assert h.frames == []


def test_repeated_pair_is_not_delivered_twice():
    h = Harness()
    assert h.poll(Response(body(pair_id=5, epoch="E1"))) == "delivered"
    assert h.poll(Response(body(pair_id=5, epoch="E1"))) == "rejected"
    assert len(h.frames) == 1


def test_epoch_change_notifies_before_delivering_the_new_frame():
    h = Harness()
    order = []
    h.backend._on_epoch_change = lambda e: order.append(("epoch", e))
    h.backend._on_pair = lambda p: order.append(("pair", p.meta["epoch"]))
    h.poll(Response(body(pair_id=9, epoch="E1")))
    h.poll(Response(body(pair_id=0, epoch="E2")))
    assert order == [("pair", "E1"), ("epoch", "E2"), ("pair", "E2")]
    assert h.backend.counters["epoch_changes"] == 1


def test_pair_is_published_before_the_frame():
    h = Harness()
    order = []
    h.backend._on_pair = lambda p: order.append("pair")
    with patch.object(oak_capture, "urlopen", side_effect=lambda *a, **k: Response(body())):
        h.backend.poll_once(h.cursor, lambda m, px: order.append("frame"))
    assert order == ["pair", "frame"]


def test_wrong_mode_is_refused_at_configure():
    with pytest.raises(ValueError):
        oak_capture.OakPairBackend().configure(640, 480, 30)
