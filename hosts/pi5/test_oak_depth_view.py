"""Depth-view colouring tests. Synthetic arrays only."""
import numpy as np
import pytest

from oak_depth_view import BEYOND_BGR, NO_DEPTH_BGR, colourise, draw_box
from oak_stereo_state import PairDepthStore
import oak_pair as op


def depth(h=8, w=8, value=0):
    return np.full((h, w), value, dtype=np.uint16)


def test_no_depth_is_black_and_distinct_from_any_valid_depth():
    d = depth()
    d[0, 0] = 1
    img = colourise(d, step=1)
    assert tuple(img[1, 1]) == NO_DEPTH_BGR
    assert tuple(img[0, 0]) != NO_DEPTH_BGR


def test_near_is_red_far_is_blue():
    d = depth()
    d[0, 0], d[0, 1] = 300, 5700
    img = colourise(d, max_mm=6000, step=1)
    b, g, r = (int(v) for v in img[0, 0])
    assert r > 200 and b < 50
    b, g, r = (int(v) for v in img[0, 1])
    assert b > 200 and r < 50


def test_beyond_scale_is_marked_but_not_black():
    d = depth(value=9000)
    img = colourise(d, max_mm=6000, step=1)
    assert tuple(img[0, 0]) == BEYOND_BGR != NO_DEPTH_BGR


def test_downsampling_shape_and_dtype():
    img = colourise(np.zeros((720, 1280), dtype=np.uint16), step=4)
    assert img.shape == (180, 320, 3) and img.dtype == np.uint8


def test_input_is_not_modified():
    d = depth(value=2000)
    before = d.copy()
    colourise(d, step=2)
    assert np.array_equal(d, before)


@pytest.mark.parametrize("bad", [np.zeros((4, 4), dtype=np.float32), np.zeros((4, 4, 3), dtype=np.uint16), None, [[1]]])
def test_wrong_input_is_rejected(bad):
    with pytest.raises(ValueError):
        colourise(bad)


@pytest.mark.parametrize("kw", [{"step": 0}, {"step": 1.5}, {"max_mm": 0}, {"max_mm": -1}])
def test_bad_parameters_are_rejected(kw):
    with pytest.raises(ValueError):
        colourise(depth(), **kw)


def test_box_is_outlined_at_downsampled_coordinates():
    img = colourise(np.zeros((40, 40), dtype=np.uint16), step=2)
    draw_box(img, (8, 8, 24, 24), step=2)
    assert tuple(img[4, 4]) == (255, 255, 255) and tuple(img[12, 12]) == (255, 255, 255)
    assert tuple(img[8, 8]) == NO_DEPTH_BGR                 # interior untouched


@pytest.mark.parametrize("roi", [None, "junk", (1, 2, 3), (30, 30, 10, 10), (500, 500, 600, 600),
                                 (float("nan"), 0, 5, 5)])
def test_malformed_or_outside_box_is_ignored(roi):
    img = colourise(np.zeros((40, 40), dtype=np.uint16), step=2)
    before = img.copy()
    draw_box(img, roi, step=2)
    assert np.array_equal(img, before)


def test_store_latest_returns_newest_pair_that_has_depth():
    def pair(seq, mm):
        dm = None if mm is None else np.full((720, 1280), mm, dtype=np.uint16)
        return op.parse_bytes(op.encode_pair(bytes(1280 * 720 * 3), dm, rgb_seq=seq, pair_id=seq)[0])
    s = PairDepthStore()
    assert s.latest_with_depth() is None
    s.put(pair(1, 2000))
    s.put(pair(2, 3000))
    s.put(pair(3, None))                                     # newest pair is RGB-only
    key, d, meta = s.latest_with_depth()
    assert key == ("test-epoch", 2) and int(d[0, 0]) == 3000 and meta["pair_id"] == 2


# ---- 3-D view payload --------------------------------------------------------------------

from oak_depth_view import CLOUD_MAGIC, cloud_payload  # noqa: E402


def test_cloud_payload_layout_and_values():
    d = np.zeros((720, 1280), dtype=np.uint16)
    d[0, 0], d[8, 16] = 1500, 65535
    thumb = np.zeros((90, 160, 3), dtype=np.uint8)
    thumb[1, 2] = (10, 20, 30)
    body = cloud_payload(d, thumb, step=8)
    assert body[:4] == CLOUD_MAGIC
    w, h = int.from_bytes(body[4:6], "little"), int.from_bytes(body[6:8], "little")
    assert (w, h) == (160, 90) and len(body) == 8 + w * h * 2 + w * h * 3
    depth = np.frombuffer(body[8:8 + w * h * 2], dtype="<u2").reshape(h, w)
    assert int(depth[0, 0]) == 1500 and int(depth[1, 2]) == 65535 and int(depth[5, 5]) == 0
    colour = np.frombuffer(body[8 + w * h * 2:], dtype=np.uint8).reshape(h, w, 3)
    assert tuple(int(v) for v in colour[1, 2]) == (10, 20, 30)


@pytest.mark.parametrize("thumb", [np.zeros((90, 159, 3), np.uint8), np.zeros((90, 160), np.uint8),
                                   np.zeros((90, 160, 3), np.float32), None])
def test_cloud_payload_refuses_mismatched_colour(thumb):
    with pytest.raises(ValueError):
        cloud_payload(np.zeros((720, 1280), dtype=np.uint16), thumb, step=8)


def test_store_keeps_each_pairs_own_colour_and_drops_it_with_the_pair():
    def pair(seq, mm, fill):
        dm = None if mm is None else np.full((720, 1280), mm, dtype=np.uint16)
        return op.parse_bytes(op.encode_pair(bytes([fill]) * (1280 * 720 * 3), dm, rgb_seq=seq, pair_id=seq)[0])
    s = PairDepthStore(capacity=2)
    s.put(pair(1, 2000, 11))
    s.put(pair(2, 3000, 22))
    assert s.thumb(("test-epoch", 1)).shape == (90, 160, 3)
    assert s.thumb(("test-epoch", 1), 4).shape == (180, 320, 3)
    assert s.thumb(("test-epoch", 1), 2).shape == (360, 640, 3)
    for bad in (1, 3, 16, 0, "4"):
        with pytest.raises(ValueError):
            s.thumb(("test-epoch", 1), bad)
    assert int(s.thumb(("test-epoch", 1))[0, 0, 0]) == 11 and int(s.thumb(("test-epoch", 2))[0, 0, 0]) == 22
    s.put(pair(3, None, 33))                                   # RGB-only pair: no colour kept
    assert s.thumb(("test-epoch", 3)) is None
    assert s.thumb(("test-epoch", 1)) is None                  # evicted with its pair
    s.clear()
    assert s.thumb(("test-epoch", 2)) is None


def test_fine_cloud_payload_matches_the_fine_thumbnail():
    d = np.full((720, 1280), 2000, dtype=np.uint16)
    body = cloud_payload(d, np.zeros((180, 320, 3), dtype=np.uint8), step=4)
    assert int.from_bytes(body[4:6], "little") == 320 and int.from_bytes(body[6:8], "little") == 180
    assert len(body) == 8 + 320 * 180 * 5
    with pytest.raises(ValueError):
        cloud_payload(d, np.zeros((90, 160, 3), dtype=np.uint8), step=4)     # coarse colour, fine depth


def test_every_demo_route_points_at_a_file_that_exists():
    import mono_demo
    for route, (name, _mime) in mono_demo.ASSETS.items():
        assert (mono_demo.ROOT / name).is_file(), route
    assert mono_demo.asset("/demo/cloud")[0].startswith("text/html")
    assert mono_demo.asset("/demo/../garuda_mono.py") is None


def test_full_detail_payload():
    d = np.full((720, 1280), 2000, dtype=np.uint16)
    body = cloud_payload(d, np.zeros((360, 640, 3), dtype=np.uint8), step=2)
    assert len(body) == 8 + 640 * 360 * 5


def test_observed_display_sampling_keeps_one_real_sample_even_at_partial_edges():
    from oak_depth_view import observed_cells, observed_cloud_payload
    import json
    import struct

    d = depth(h=3, w=5)
    d[1, 1], d[1, 2], d[2, 4] = 1000, 3000, 900
    rgb = np.arange(45, dtype=np.uint8).reshape(3, 5, 3)
    chosen, offsets = observed_cells(d, 2)
    assert chosen.tolist() == [[1000, 3000, 0], [0, 0, 900]]
    assert offsets.tolist() == [[3, 2, 0], [0, 0, 0]]
    assert np.array_equal(colourise(d), colourise(chosen, step=1))
    body = observed_cloud_payload(d, rgb, 2, {})
    assert body.startswith(b'GCL3')
    start = 8 + struct.unpack_from('<I', body, 4)[0]
    assert json.loads(body[8:start])['width'] == 3
    assert np.frombuffer(body, '<u2', 6, start).tolist() == list(chosen.flat)
    colours = np.frombuffer(body, np.uint8, 18, start+12).reshape(6, 3)
    assert np.array_equal(colours[[0, 1, 5]], rgb[[1, 1, 2], [1, 2, 4]])
    assert list(body[-6:]) == list(offsets.flat)
