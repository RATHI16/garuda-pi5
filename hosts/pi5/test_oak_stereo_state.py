"""Pair-to-frame matching and the stereo state block. Synthetic data, no device."""
import numpy as np
import pytest

import oak_pair as op
from oak_stereo_state import PairDepthStore, frame_key, stereo_measurement

W, H = 1280, 720
RGB = bytes(W * H * 3)
BOX = (500, 150, 240, 520)


def pair(seq, mm=None, epoch="E1", pair_id=None, reason="pair_wait_expired"):
    depth = None if mm is None else np.full((H, W), mm, dtype=np.uint16)
    body, _ = op.encode_pair(RGB, depth, epoch=epoch, rgb_seq=seq, depth_seq=seq,
                             pair_id=seq if pair_id is None else pair_id, reason=reason)
    return op.parse_bytes(body)


def test_frame_key_from_frame_meta_fields():
    assert frame_key("oak-rgb:E1", 41) == ("E1", 41)
    assert frame_key("oak-rgb:a:b", 2) == ("a:b", 2)
    assert frame_key("/dev/video0", 3) is None
    assert frame_key(None, 3) is None


def test_measurement_uses_the_processed_frames_own_depth():
    s = PairDepthStore()
    s.put(pair(10, 2000))
    s.put(pair(11, 3000))                       # newer pair arrives while frame 10 is in inference
    r = stereo_measurement(s, ("E1", 10), BOX)
    assert r["status"] == "ok" and r["z_m"] == pytest.approx(2.0)
    assert r["pair"] == {"epoch": "E1", "rgb_seq": 10, "pair_id": 10}


def test_no_latest_depth_fallback_when_the_frames_pair_is_missing():
    s = PairDepthStore()
    s.put(pair(11, 3000))
    r = stereo_measurement(s, ("E1", 10), BOX)
    assert (r["status"], r["reason"], r["z_m"]) == ("unavailable", "no_pair_for_frame", None)


def test_rgb_only_pair_reports_the_producers_reason_not_a_neighbours_depth():
    s = PairDepthStore()
    s.put(pair(9, 2000))
    s.put(pair(10, None, reason="skew_exceeded"))
    r = stereo_measurement(s, ("E1", 10), BOX)
    assert (r["status"], r["reason"], r["z_m"]) == ("unavailable", "skew_exceeded", None)
    assert r["pair_skew_ns"] is None


def test_same_sequence_from_another_epoch_does_not_match():
    s = PairDepthStore()
    s.put(pair(10, 2000, epoch="OLD"))
    assert stereo_measurement(s, ("NEW", 10), BOX)["reason"] == "no_pair_for_frame"


def test_no_target_and_coasting_target_give_no_measurement():
    s = PairDepthStore()
    s.put(pair(10, 2000))
    assert stereo_measurement(s, ("E1", 10), None)["reason"] == "no_target"
    r = stereo_measurement(s, ("E1", 10), BOX, coasting=True)
    assert (r["status"], r["reason"], r["z_m"]) == ("unavailable", "target_coasting", None)


def test_non_oak_frame_is_unavailable():
    r = stereo_measurement(PairDepthStore(), None, BOX)
    assert (r["status"], r["reason"], r["pair"]) == ("unavailable", "not_an_oak_pair_frame", None)


def test_estimator_statuses_pass_through_with_scores_and_reference_frame():
    s = PairDepthStore()
    p = pair(10, 2500)
    d = p.depth_mm.copy()
    d[:, : W // 2] = 0
    d[:, 620:] = 1500                            # two depths inside the torso region
    d[:, 500:620] = 2500
    s._items[("E1", 10)] = (d, p.meta)
    r = stereo_measurement(s, ("E1", 10), BOX)
    assert r["status"] == "ambiguous" and r["z_m"] is None and len(r["candidates"]) == 2
    assert r["depth_reference_frame"] == "unverified"
    assert 0.0 <= r["coverage"] <= 1.0 and 0.0 <= r["support"] <= 1.0


def test_store_is_bounded_and_drops_oldest():
    s = PairDepthStore(capacity=3)
    for i in range(6):
        s.put(pair(i, 2000))
    assert len(s) == 3
    assert s.get(("E1", 2)) is None and s.get(("E1", 5)) is not None


def test_clear_forgets_everything():
    s = PairDepthStore()
    s.put(pair(1, 2000))
    s.clear()
    assert len(s) == 0 and stereo_measurement(s, ("E1", 1), BOX)["reason"] == "no_pair_for_frame"


def test_block_always_has_the_same_keys():
    s = PairDepthStore()
    s.put(pair(10, 2000))
    keys = None
    for r in (stereo_measurement(s, None, BOX), stereo_measurement(s, ("E1", 99), BOX),
              stereo_measurement(s, ("E1", 10), None), stereo_measurement(s, ("E1", 10), BOX)):
        base = set(r) - {"candidates", "estimator_config"}
        keys = keys or base
        assert base == keys
    assert "standoff_m" not in keys and "range_m" not in keys


# ---- epoch gate (review astra-0007: restart race) ----------------------------------------

from oak_stereo_state import EpochGate  # noqa: E402


def test_gate_first_frame_is_not_a_reset():
    g = EpochGate()
    assert g.entering("A") is False and g.entering("A") is False


def test_gate_detects_change_exactly_once():
    g = EpochGate()
    g.entering("A")
    assert g.entering("B") is True
    assert g.entering("B") is False


def test_restart_interleaving_cannot_lose_the_reset():
    """The schedule from the review: the restart is announced, the inference loop wakes on the
    OLD frame (already processed, so it skips), then the new epoch's frame arrives."""
    g = EpochGate()
    processed_seq, resets = None, []

    def loop_iteration(frame):                   # mirrors loop(): skip check BEFORE the gate
        nonlocal processed_seq
        if frame["seq"] == processed_seq:
            return "skipped"
        processed_seq = frame["seq"]
        resets.append(g.entering(frame["epoch"]))
        return "processed"

    old = {"seq": 7, "epoch": "A"}
    assert loop_iteration(old) == "processed"
    # owner restarts here; on_epoch_change has run, on_frame for the new run has not
    assert loop_iteration(old) == "skipped"      # a flag-based design cleared its flag here
    assert loop_iteration(old) == "skipped"
    new = {"seq": 8, "epoch": "B"}
    assert loop_iteration(new) == "processed"
    assert resets == [False, True]


def test_gate_change_back_to_an_earlier_epoch_is_still_a_reset():
    g = EpochGate()
    g.entering("A"); g.entering("B")
    assert g.entering("A") is True


def test_old_epoch_result_is_not_published_after_new_epoch_frame_arrived():
    assert EpochGate.may_publish("A", "A") is True
    assert EpochGate.may_publish("A", "B") is False


# ---- review astra-0009: target class and freshness ---------------------------------------

from mono_state import fresh_stereo  # noqa: E402


@pytest.mark.parametrize("cls", ["chair", "bottle", "", None, "Person"])
def test_non_person_target_gets_no_torso_depth(cls):
    s = PairDepthStore()
    s.put(pair(10, 2000))
    r = stereo_measurement(s, ("E1", 10), BOX, target_class=cls)
    assert (r["status"], r["reason"], r["z_m"]) == ("unavailable", "unsupported_target_class", None)


def test_person_target_is_measured():
    s = PairDepthStore()
    s.put(pair(10, 2000))
    assert stereo_measurement(s, ("E1", 10), BOX, target_class="person")["status"] == "ok"


def test_no_target_reason_wins_over_class_reason():
    s = PairDepthStore()
    s.put(pair(10, 2000))
    assert stereo_measurement(s, ("E1", 10), None, target_class=None)["reason"] == "no_target"


@pytest.mark.parametrize("age", [None, 1500, 60000, -5])
def test_stale_or_unknown_age_block_loses_its_value(age):
    s = PairDepthStore()
    s.put(pair(10, 2000))
    ok = stereo_measurement(s, ("E1", 10), BOX)
    shown = fresh_stereo(ok, age)
    assert (shown["status"], shown["reason"], shown["z_m"]) == ("unavailable", "stale_state", None)
    assert shown["pair"] == ok["pair"] and "candidates" not in shown
    assert ok["status"] == "ok"                          # the original block is not mutated


def test_fresh_block_passes_through_unchanged():
    s = PairDepthStore()
    s.put(pair(10, 2000))
    ok = stereo_measurement(s, ("E1", 10), BOX)
    assert fresh_stereo(ok, 120) is ok
    assert fresh_stereo(None, 120) is None


def test_estimator_config_identity_is_reported_where_the_estimator_ran():
    from oak_distance import Params, params_id
    s = PairDepthStore()
    s.put(pair(10, 2000))
    r = stereo_measurement(s, ("E1", 10), BOX)
    assert r["estimator_config"] == params_id() and len(r["estimator_config"]) == 12
    assert params_id(Params(dominance=0.5)) != params_id()
    assert "estimator_config" not in stereo_measurement(s, ("E1", 10), None)


# ---- depth view selection (review astra-0019) --------------------------------------------

from oak_stereo_state import view_selection, VIEW_STALE_NS  # noqa: E402

NOW = 10_000_000_000


def vpair(seq, mm, host_ns):
    depth = None if mm is None else np.full((H, W), mm, dtype=np.uint16)
    body, _ = op.encode_pair(RGB, depth, rgb_seq=seq, depth_seq=seq, pair_id=seq,
                             rgb_device_ns=1_000, depth_device_ns=1_000, host_offset_ns=host_ns - 1_000)
    return op.parse_bytes(body)


def block(seq, roi):
    return {"status": "ok", "roi": roi, "pair": {"epoch": "test-epoch", "rgb_seq": seq, "pair_id": seq}}


def test_outline_and_depth_come_from_the_same_pair_when_the_box_moves():
    s = PairDepthStore()
    s.put(vpair(10, 2000, NOW - 300_000_000))      # the frame inference measured
    s.put(vpair(11, 3000, NOW - 100_000_000))      # a newer frame; the person has moved since
    key, depth, roi = view_selection(s, block(10, (100, 100, 200, 200)), NOW)
    assert key == ("test-epoch", 10) and int(depth[0, 0]) == 2000 and roi == (100, 100, 200, 200)


def test_no_outline_when_the_measured_pair_is_no_longer_stored():
    s = PairDepthStore(capacity=2)
    for seq in (11, 12):
        s.put(vpair(seq, 3000, NOW - 100_000_000))
    key, depth, roi = view_selection(s, block(10, (100, 100, 200, 200)), NOW)
    assert key == ("test-epoch", 12) and roi is None            # newest depth, never an old outline


def test_no_outline_without_a_measurement():
    s = PairDepthStore()
    s.put(vpair(11, 3000, NOW - 100_000_000))
    for blk in (None, {}, {"status": "unavailable", "pair": None, "roi": None}):
        assert view_selection(s, blk, NOW)[2] is None


def test_measured_pair_without_depth_falls_back_to_newest_without_outline():
    s = PairDepthStore()
    s.put(vpair(9, 2500, NOW - 400_000_000))
    s.put(vpair(10, None, NOW - 300_000_000))
    key, _, roi = view_selection(s, block(10, (1, 2, 3, 4)), NOW)
    assert key == ("test-epoch", 9) and roi is None


def test_outage_without_restart_stops_the_view():
    s = PairDepthStore()
    s.put(vpair(10, 2000, NOW - 200_000_000))
    assert view_selection(s, block(10, (1, 2, 3, 4)), NOW) is not None
    later = NOW + 5_000_000_000                                    # capture stopped; store unchanged
    assert view_selection(s, block(10, (1, 2, 3, 4)), later) is None
    assert view_selection(s, None, later) is None


def test_freshness_boundary_and_future_timestamps():
    s = PairDepthStore()
    s.put(vpair(10, 2000, NOW))
    assert view_selection(s, None, NOW + VIEW_STALE_NS - 1) is not None
    assert view_selection(s, None, NOW + VIEW_STALE_NS) is None
    assert view_selection(s, None, NOW - 1) is None                # timestamp ahead of the clock


def test_stale_measured_pair_does_not_block_a_fresh_newer_one():
    s = PairDepthStore()
    s.put(vpair(10, 2000, NOW - 3_000_000_000))
    s.put(vpair(11, 3000, NOW - 100_000_000))
    key, _, roi = view_selection(s, block(10, (1, 2, 3, 4)), NOW)
    assert key == ("test-epoch", 11) and roi is None


def test_empty_store():
    assert view_selection(PairDepthStore(), block(1, (1, 2, 3, 4)), NOW) is None


def test_qualification_requires_explicit_producer_claim():
    from oak_stereo_state import _block
    assert _block('unavailable','missing')['measurement_qualified'] is False
    assert _block('ok','',key=('e',1),meta={'pair_id':1,'depth_reference_frame':'rgb_optical','pair_skew_ns':0,'measurement_qualified':True,'preview_only':True})['preview_only'] is True
    assert _block('ok','',key=('e',1),meta={'pair_id':1,'depth_reference_frame':'rgb_optical','pair_skew_ns':0})['measurement_qualified'] is False
