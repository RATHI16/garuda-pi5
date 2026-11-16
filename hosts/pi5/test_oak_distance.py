"""Synthetic-depth tests for the person stereo-depth estimator. No device, no real images.

These check the estimator's logic only. They say nothing about measured distance accuracy."""
import numpy as np
import pytest

from oak_distance import Params, estimate_person_z

W, H = 1280, 720
BG = 6000          # background wall, mm
PERSON = 2500      # person, mm
BOX = (500, 150, 240, 520)   # x, y, w, h in pixels (top-left origin)


def scene(person_mm=PERSON, bg_mm=BG, box=BOX):
    """Background everywhere, a person-shaped slab (70 % of the box width, centred)."""
    d = np.full((H, W), bg_mm, dtype=np.uint16)
    x, y, w, h = box
    d[y:y + h, x + int(0.15 * w):x + int(0.85 * w)] = person_mm
    return d


def kpts_for(box=BOX, conf=0.9):
    """17 COCO keypoints; only shoulders (5, 6) and hips (11, 12) are placed meaningfully."""
    x, y, w, h = box
    k = [[x + w / 2, y + h / 2, 0.0] for _ in range(17)]
    k[5] = [x + 0.30 * w, y + 0.20 * h, conf]
    k[6] = [x + 0.70 * w, y + 0.20 * h, conf]
    k[11] = [x + 0.35 * w, y + 0.55 * h, conf]
    k[12] = [x + 0.65 * w, y + 0.55 * h, conf]
    return k


def test_plain_person_from_box():
    r = estimate_person_z(scene(), BOX)
    assert r["status"] == "ok"
    assert r["z_m"] == pytest.approx(2.5)
    assert r["roi_source"] == "box"
    assert r["coverage"] == pytest.approx(1.0)
    assert r["support"] == pytest.approx(1.0)
    assert "standoff_m" not in r


def test_plain_person_from_keypoints():
    r = estimate_person_z(scene(), BOX, kpts_for())
    assert r["status"] == "ok"
    assert r["z_m"] == pytest.approx(2.5)
    assert r["roi_source"] == "keypoints"


def test_low_confidence_keypoints_fall_back_to_box():
    r = estimate_person_z(scene(), BOX, kpts_for(conf=0.2))
    assert r["status"] == "ok" and r["roi_source"] == "box"


def test_missing_or_malformed_keypoints_fall_back_to_box():
    assert estimate_person_z(scene(), BOX, None)["roi_source"] == "box"
    assert estimate_person_z(scene(), BOX, [[1, 2, 0.9]] * 5)["roi_source"] == "box"


def test_background_leakage_does_not_pull_the_value():
    d = scene()
    x, y, w, h = BOX
    roi = estimate_person_z(d, BOX)["roi"]
    x0, y0, x1, y1 = roi
    d[y0:y1, x0:x0 + (x1 - x0) // 4] = BG          # a quarter of the region sees the wall
    r = estimate_person_z(d, BOX)
    assert r["status"] == "ok"
    assert r["z_m"] == pytest.approx(2.5)
    assert r["support"] == pytest.approx(0.75, abs=0.02)


def test_small_foreground_object_is_not_selected():
    d = scene()
    x0, y0, x1, y1 = estimate_person_z(d, BOX)["roi"]
    d[y0:y0 + (y1 - y0) // 5, x0:x1] = 900          # a hand/object close to the camera, 20 %
    r = estimate_person_z(d, BOX)
    assert r["status"] == "ok"
    assert r["z_m"] == pytest.approx(2.5)           # torso, not the nearest thing


def test_two_comparable_depths_are_ambiguous_not_guessed():
    d = scene()
    x0, y0, x1, y1 = estimate_person_z(d, BOX)["roi"]
    d[y0:y1, x0:(x0 + x1) // 2] = 1500              # second person covers half the region
    r = estimate_person_z(d, BOX)
    assert r["status"] == "ambiguous"
    assert r["reason"] == "competing_depth_clusters"
    assert r["z_m"] is None
    zs = sorted(c["z_m"] for c in r["candidates"])
    assert zs == pytest.approx([1.5, 2.5])


def test_all_invalid_region():
    d = scene()
    x, y, w, h = BOX
    d[y:y + h, x:x + w] = 0
    r = estimate_person_z(d, BOX)
    assert (r["status"], r["reason"], r["z_m"]) == ("invalid", "no_valid_depth", None)
    assert r["coverage"] == 0.0


def test_sparse_depth_is_invalid_and_reports_coverage():
    d = scene()
    x0, y0, x1, y1 = estimate_person_z(d, BOX)["roi"]
    mask = np.ones((y1 - y0, x1 - x0), dtype=bool)
    mask[::4, ::4] = False                           # keep 1 pixel in 16
    d[y0:y1, x0:x1][mask] = 0
    r = estimate_person_z(d, BOX)
    assert r["status"] == "invalid" and r["reason"] == "insufficient_valid_depth"
    assert r["coverage"] == pytest.approx(1 / 16, abs=0.01)


def test_evenly_scattered_depth_is_not_a_surface():
    rng = np.random.default_rng(0)
    d = rng.integers(800, 9000, size=(H, W)).astype(np.uint16)
    r = estimate_person_z(d, BOX)
    assert r["status"] == "invalid" and r["reason"] == "cluster_too_spread"
    assert r["z_m"] is None


def test_many_small_clusters_have_no_dominant_one():
    d = scene()
    x0, y0, x1, y1 = estimate_person_z(d, BOX)["roi"]
    levels = [1000, 2000, 3000, 4500, 6500, 9000, 12000, 16000]   # eight far-apart depths
    cols = np.arange(x1 - x0) % len(levels)
    d[y0:y1, x0:x1] = np.array(levels, dtype=np.uint16)[cols][None, :]
    r = estimate_person_z(d, BOX)
    assert r["status"] == "invalid" and r["reason"] == "no_dominant_cluster"


def test_slanted_torso_within_spread_limit_is_ok():
    d = scene()
    x0, y0, x1, y1 = estimate_person_z(d, BOX)["roi"]
    ramp = np.linspace(2400, 2700, x1 - x0).astype(np.uint16)       # 0.3 m front to back
    d[y0:y1, x0:x1] = ramp[None, :]
    r = estimate_person_z(d, BOX)
    assert r["status"] == "ok"
    assert r["z_m"] == pytest.approx(2.55, abs=0.01)


def test_tiny_box_is_invalid():
    r = estimate_person_z(scene(), (600, 300, 12, 20))
    assert r["status"] == "invalid" and r["reason"] == "roi_too_small"


def test_box_clipped_by_image_edge_still_works():
    box = (W - 120, 150, 240, 520)                   # half outside on the right
    r = estimate_person_z(scene(box=(W - 240, 150, 240, 520)), box)
    assert r["status"] in ("ok", "invalid")
    x0, y0, x1, y1 = r["roi"]
    assert 0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H


def test_box_fully_outside_is_invalid():
    r = estimate_person_z(scene(), (W + 50, 100, 200, 400))
    assert r["status"] == "invalid" and r["reason"] == "roi_too_small"
    assert r["roi"] is None


def test_noisy_person_reports_median():
    rng = np.random.default_rng(1)
    d = scene()
    x, y, w, h = BOX
    noise = rng.normal(0, 30, size=(h, w))
    d[y:y + h, x:x + w] = np.where(d[y:y + h, x:x + w] == PERSON,
                                   (PERSON + noise).astype(np.uint16), d[y:y + h, x:x + w])
    r = estimate_person_z(d, BOX)
    assert r["status"] == "ok"
    assert r["z_m"] == pytest.approx(2.5, abs=0.02)


def test_scores_are_separate_and_bounded():
    r = estimate_person_z(scene(), BOX)
    assert 0.0 <= r["coverage"] <= 1.0 and 0.0 <= r["support"] <= 1.0
    assert set(r) >= {"status", "reason", "z_m", "coverage", "support", "roi", "roi_source"}


def test_wrong_depth_dtype_or_shape_is_rejected():
    with pytest.raises(ValueError):
        estimate_person_z(np.zeros((H, W), dtype=np.float32), BOX)
    with pytest.raises(ValueError):
        estimate_person_z(np.zeros((H, W, 3), dtype=np.uint16), BOX)


def test_params_are_overridable():
    d = scene()
    x0, y0, x1, y1 = estimate_person_z(d, BOX)["roi"]
    d[y0:y1, x0:x0 + (x1 - x0) * 45 // 100] = BG     # person keeps 55 %
    assert estimate_person_z(d, BOX)["status"] == "ambiguous"
    assert estimate_person_z(d, BOX, params=Params(dominance=0.5))["status"] == "ok"


NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize("box", [
    (NAN, 150, 240, 520), (500, INF, 240, 520), (500, 150, -240, 520), (500, 150, 240, 0),
    (500, 150, 240), None, "box", (500, 150, "wide", 520), (500, 150, None, 520),
])
def test_malformed_box_is_invalid_and_never_raises(box):
    r = estimate_person_z(scene(), box)
    assert (r["status"], r["reason"], r["z_m"], r["roi"]) == ("invalid", "bad_box", None, None)


def _with(k_index, value):
    k = kpts_for()
    k[k_index] = value
    return k


@pytest.mark.parametrize("kpts", [
    _with(5, [NAN, 200.0, 0.9]), _with(6, [600.0, INF, 0.9]), _with(11, [600.0, 400.0, NAN]),
    _with(12, [600.0, 400.0]), _with(5, None), _with(6, "junk"), _with(11, [None, None, 0.9]),
    "not-a-list", 42, [None] * 17, {},
])
def test_malformed_keypoints_fall_back_to_box_and_never_raise(kpts):
    r = estimate_person_z(scene(), BOX, kpts)
    assert r["status"] == "ok" and r["roi_source"] == "box"
    assert r["z_m"] == pytest.approx(2.5)
