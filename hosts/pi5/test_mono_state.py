from mono_state import build_snapshot, capabilities

STATE = {"class": "person", "raw": 2.5, "t": 100.0, "count": 2,
         "tracks_full": [(3, "person", 10.0, 20.0, 30.0, 40.0, 0.9, False),
                         (7, "chair", 50.0, 60.0, 70.0, 80.0, 0.4, True)]}
EXTRAS = {"lock_id": 3, "lock_coasting": False, "est_m": 2.4, "valid": True,
          "depth_engine": "y26d", "detector_mode": "primary", "prompt": [],
          "cal_n": 0, "fit": None, "age_ms": 120, "frame_w": 1280, "frame_h": 720}

def test_snapshot_shape_and_tracks():
    s = build_snapshot(STATE, EXTRAS)
    assert s["v"] == 2 and s["age_ms"] == 120
    assert s["tracks"][0] == {"id": 3, "label": "person", "conf": 0.9,
                              "x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0, "coasting": False}
    assert s["lock"] == {"id": 3, "class": "person", "coasting": False}
    assert s["distance"] == {"raw_m": 2.5, "est_m": 2.4, "valid": True}
    assert s["frame"] == {"width": 1280, "height": 720, "capture_host_ns":100_000_000_000}

def test_snapshot_no_lock_no_distance():
    s = build_snapshot({**STATE, "raw": None}, {**EXTRAS, "lock_id": None,
                                                "est_m": None, "valid": False})
    assert s["lock"]["id"] is None
    assert s["distance"] == {"raw_m": None, "est_m": None, "valid": False}

def test_snapshot_tolerates_short_track_tuples():
    # pre-migration 6-tuples (no conf/coasting) must not crash: conf 0.0, coasting False
    s = build_snapshot({**STATE, "tracks_full": [(1, "person", 1.0, 2.0, 3.0, 4.0)]}, EXTRAS)
    assert s["tracks"][0]["conf"] == 0.0 and s["tracks"][0]["coasting"] is False


def test_v2_tracks_carry_kpts_and_gesture():
    kp = [[1.0, 2.0, 0.9]] * 17
    st = {**STATE, "tracks_full": [(3, "person", 10.0, 20.0, 30.0, 40.0, 0.9, False, kp)]}
    s = build_snapshot(st, {**EXTRAS, "gesture": {"name": "stop", "track_id": 3, "held_ms": 1200}})
    assert s["v"] == 2
    assert s["tracks"][0]["kpts"] == kp
    assert s["gesture"] == {"name": "stop", "track_id": 3, "held_ms": 1200}


def test_v2_kpts_absent_when_not_pose_mode():
    s = build_snapshot(STATE, EXTRAS)          # 8-tuples, no gesture extra
    assert "kpts" not in s["tracks"][0]
    assert s["gesture"] == {"name": None, "track_id": None, "held_ms": 0}


def test_attach_kpts_matches_by_iou():
    from mono_state import attach_kpts
    kp = [[5.0, 5.0, 0.9]] * 17
    class D:  # duck-typed det
        def __init__(self, x, y, w, h, kpts): self.x, self.y, self.w, self.h, self.kpts = x, y, w, h, kpts
    tf = [(1, "person", 10.0, 10.0, 100.0, 200.0, 0.9, False)]
    out = attach_kpts(tf, [D(12.0, 11.0, 98.0, 205.0, kp), D(500.0, 500.0, 50.0, 50.0, None)])
    assert out[0][8] == kp
    out2 = attach_kpts(tf, [D(500.0, 500.0, 50.0, 50.0, kp)])   # no overlap -> no kpts
    assert len(out2[0]) == 8


def test_snapshot_carries_stats_passthrough():
    st = build_snapshot(STATE, {**EXTRAS, "stats": {"fps": 24.8, "unique_total": 17, "temp_c": 61.2}})
    assert st["stats"] == {"fps": 24.8, "unique_total": 17, "temp_c": 61.2}


def test_snapshot_stats_default_empty():
    assert build_snapshot(STATE, EXTRAS)["stats"] == {}


def test_invalid_fit_is_not_replaced_with_raw_distance():
    for value in (-1.35, 0, float('nan'), float('inf'), 100):
        d = build_snapshot(STATE, {**EXTRAS, "est_m": value})["distance"]
        assert d == {"raw_m": None, "est_m": None, "valid": False}


def test_stale_range_is_not_exposed_as_raw_fallback():
    assert build_snapshot(STATE, {**EXTRAS, "valid": False})["distance"]["raw_m"] is None


def test_relative_depth_is_not_labelled_metres():
    s = build_snapshot(STATE, {**EXTRAS, "depth_engine": "da", "est_m": None})
    assert s["distance"] == {"raw_m": None, "est_m": None, "valid": False}


def test_relative_depth_can_have_large_raw_values_with_valid_calibration():
    s = build_snapshot({**STATE, "raw": 1000}, {**EXTRAS, "depth_engine": "da", "est_m": 2.5})
    assert s["distance"] == {"raw_m": None, "est_m": 2.5, "valid": True}


def test_snapshot_carries_capture_identity_and_actual_dimensions():
    s = build_snapshot({**STATE, "frame_seq": 22, "frame_source": "/dev/video0",
                        "frame_epoch": "abc:0", "frame_w": 640, "frame_h": 480}, EXTRAS)
    assert s["frame"] == {"seq": 22, "source": "/dev/video0", "epoch": "abc:0", "width": 640, "height": 480, "capture_host_ns":100_000_000_000}


def test_appearance_is_attached_only_to_its_own_track():
    descriptor = [0.0] * 128
    s = build_snapshot({**STATE, "appearances": {3: descriptor}}, EXTRAS)
    assert s["tracks"][0]["appearance"] == descriptor
    assert "appearance" not in s["tracks"][1]


def test_face_and_gesture_are_attached_to_the_correct_subject():
    face = {"embedding": [0]*128, "sample": "capture-1", "age_ms": 50}
    gesture = {"name": "stop", "held_ms": 1100}
    s = build_snapshot({**STATE, "faces": {3: face}, "track_gestures": {7: gesture}}, EXTRAS)
    assert s["tracks"][0]["face"] == face
    assert "face" not in s["tracks"][1]
    assert s["tracks"][1]["gesture"] == gesture


def test_explicit_selection_never_switches_to_bystander():
    from types import SimpleNamespace
    from mono_state import select_track
    a = SimpleNamespace(track_id=1, cls="person", time_since_update=9, conf=.6)
    b = SimpleNamespace(track_id=2, cls="person", time_since_update=0, conf=.9)
    assert select_track([a, b], 1, "person", True, 5) is a
    assert select_track([b], 1, "person", True, 5) is None
    assert select_track([a, b], 1, "person", False, 5) is b


def test_stereo_block_is_additive_and_absent_by_default():
    assert "stereo" not in build_snapshot(STATE, EXTRAS)
    block = {"status": "ok", "reason": "ok", "z_m": 2.5}
    snap = build_snapshot({**STATE, "stereo": block}, EXTRAS)
    assert snap["stereo"] == block
    old = build_snapshot({**STATE, "stereo": block}, {**EXTRAS, "age_ms": 5000})
    assert (old["stereo"]["status"], old["stereo"]["reason"], old["stereo"]["z_m"]) == \
        ("unavailable", "stale_state", None)
    assert snap["distance"] == build_snapshot(STATE, EXTRAS)["distance"]   # distance untouched


def test_snapshot_keeps_capture_pair_separate_from_local_sequence():
    s = build_snapshot({**STATE, 'frame_seq': 44, 'pair_key': ('camera-run', 900)}, EXTRAS)
    assert s['frame']['capture_pair'] == {'epoch':'camera-run','rgb_seq':900}
    assert s['frame']['seq'] == 44


def test_capabilities_name_one_distance_source_per_camera():
    oak = capabilities("oak-rgb", True, False, "y26d")
    assert oak == {"camera": "oak", "distance_source": "stereo", "mono_engine": None, "depth_map": True, "cloud": True,
                   "mount": "fixed"}
    cm3 = capabilities("csi", False, True, "y26d")
    assert cm3 == {"camera": "cm3", "distance_source": "mono_estimate", "mono_engine": "y26d", "depth_map": False, "cloud": False,
                   "mount": "fixed"}
    assert capabilities("/dev/video8", False, True, "da")["camera"] == "usb"


def test_capabilities_never_claim_stereo_for_a_one_lens_camera_or_invent_a_distance():
    # A stereo flag left on in the service settings must not make the CM3 or a webcam "stereo".
    for source in ("csi", "/dev/video0", None, "something-else"):
        c = capabilities(source, True, True, "y26d")
        assert c["distance_source"] == "mono_estimate" and not c["depth_map"] and not c["cloud"]
    # Colour-only OAK with mono depth switched off (the standing demo before a lock): no distance.
    assert capabilities("oak-rgb", False, False, "y26d")["distance_source"] == "none"
    assert capabilities("oak-rgb", False, False, "y26d")["mono_engine"] is None
    # OAK without stereo but with the mono network on is an estimate, and says so.
    assert capabilities("oak-rgb", False, True, "da")["distance_source"] == "mono_estimate"


# --- camera-on-vehicle shadow command -----------------------------------------------------
from mono_state import mount_setting, onboard_obs, fresh_onboard

ROWS = [(3, "person", 100.0, 100.0, 200.0, 400.0, 0.9, False), (7, "person", 540.0, 150.0, 200.0, 400.0, 0.8, False)]
STEREO_OK = {"status": "ok", "z_m": 3.0, "reason": "ok"}


def test_mount_is_locked_only_when_the_service_states_it():
    assert mount_setting(None) == ("fixed", False)
    assert mount_setting("") == ("fixed", False) and mount_setting("drone") == ("fixed", False)
    assert mount_setting("vehicle") == ("vehicle", True) and mount_setting("fixed") == ("fixed", True)
    assert capabilities("csi", False, True, "y26d", "vehicle")["mount"] == "vehicle"
    assert capabilities("csi", False, True, "y26d", "airborne")["mount"] == "fixed"


def test_onboard_distance_source_follows_the_camera_not_the_frame():
    oak = capabilities("oak-rgb", True, False, None, "vehicle")
    o = onboard_obs(ROWS, 7, False, 1280, 720, 50, None, oak, STEREO_OK, True)
    assert o["range_source"] == "stereo" and o["stereo"] is STEREO_OK and o["target"]["id"] == 7 and o["target"]["x"] == 540.0
    # OAK with stereo switched off: no distance at all, never box height.
    assert onboard_obs(ROWS, 7, False, 1280, 720, 50, None, capabilities("oak-rgb", False, True, "y26d"), STEREO_OK, True)["range_source"] == "none"
    for src in ("csi", "/dev/video8"):
        c = onboard_obs(ROWS, 7, False, 1280, 720, 50, None, capabilities(src, True, True, "y26d"), STEREO_OK, True)
        assert c["range_source"] == "box_height" and c["stereo"] is None     # a stray stereo block is not passed on
    assert onboard_obs(ROWS, 7, False, 1280, 720, 50, None, capabilities(None, False, True, "y26d"), None, True)["range_source"] == "none"


def test_onboard_target_and_identity_mapping():
    caps = capabilities("oak-rgb", True, False, None)
    assert onboard_obs(ROWS, None, False, 1280, 720, 50, None, caps, None, True)["target"] is None
    assert onboard_obs(ROWS, 99, False, 1280, 720, 50, None, caps, None, True)["target"] is None
    assert onboard_obs(ROWS, 7, True, 1280, 720, 50, None, caps, None, True)["target"]["coasting"] is True
    good = {"target": 7, "confirmed": True, "state": "tracking"}
    assert onboard_obs(ROWS, 7, False, 1280, 720, 50, good, caps, None, True)["identity"] is True
    for bad in (dict(good, confirmed=False), dict(good, state="searching"), dict(good, target=3)):
        assert onboard_obs(ROWS, 7, False, 1280, 720, 50, bad, caps, None, True)["identity"] is False
    assert onboard_obs(ROWS, 7, False, 1280, 720, 50, None, caps, None, True)["identity"] is None
    # The detector's automatic lock (nobody selected this person) is never a vehicle target.
    assert onboard_obs(ROWS, 7, False, 1280, 720, 50, None, caps, None, False)["target"] is None
    assert onboard_obs(ROWS, 7, False, 1280, 720, 50, None, caps, None)["target"] is None


def test_published_command_does_not_outlive_its_frame():
    block = {"valid": True, "reason": "tracking", "yaw_rate": 0.2, "forward_vel": 0.5, "vertical_vel": 0.0,
             "range_m": 3.0, "forward_reason": "ok", "transmit": True}
    live = fresh_onboard(block, 100, 400)
    assert live["forward_vel"] == 0.5 and live["transmit"] is False        # never marked as transmitted
    for age in (401, None, "x"):
        old = fresh_onboard(block, age, 400)
        assert old["valid"] is False and old["reason"] == "stale frame" and old["forward_vel"] == 0.0 and old["yaw_rate"] == 0.0
    assert fresh_onboard(None, 10, 400) is None
    assert block["forward_vel"] == 0.5 and block["transmit"] is True       # input not modified
    snap = build_snapshot({"onboard": block, "tracks_full": [], "t": 1.0}, {"age_ms": 900})
    assert snap["onboard"]["valid"] is False
    assert "onboard" not in build_snapshot({"tracks_full": [], "t": 1.0}, {"age_ms": 10})


def test_carry_around_record_is_numbers_and_short_text_only():
    from mono_record import onboard_record
    import json
    r = onboard_record(5, 12.3456789, {"valid": True, "reason": "tracking", "yaw_rate": 0.123456, "forward_vel": float("nan"),
                                       "forward_reason": "ok", "range_m": 2.34567, "range_source": "stereo", "target_id": 7},
                       (1.26, 2.0, 3.0, 4.0), {"status": "ok", "reason": "ok", "z_m": 2.3, "roi": [1, 2, 3, 4]}, 61.27)
    assert r["yaw_rate"] == 0.1235 and r["forward_vel"] is None and r["range_m"] == 2.346 and r["box"] == [1.3, 2.0, 3.0, 4.0]
    assert r["stereo"] == {"status": "ok", "reason": "ok"} and r["temp_c"] == 61.3
    json.dumps(r, allow_nan=False)
    empty = onboard_record(6, 1.0, None, None, None, None)
    assert empty["valid"] is False and empty["box"] is None and empty["stereo"] is None


def test_only_a_person_is_ever_a_vehicle_target():
    """astra-0091 finding 2: the page and API can pin a chair; it must not become a target."""
    rows = [(7, "chair", 540.0, 150.0, 200.0, 400.0, 0.9, False), (8, "person", 100.0, 150.0, 200.0, 400.0, 0.9, False)]
    caps = capabilities("oak-rgb", True, False, None)
    assert onboard_obs(rows, 7, False, 1280, 720, 50, None, caps, STEREO_OK, True)["target"] is None
    assert onboard_obs(rows, 8, False, 1280, 720, 50, None, caps, STEREO_OK, True)["target"]["id"] == 8


def test_send_request_reaches_the_published_state_only_when_given():
    from mono_transmit import Transmit
    block = Transmit().snapshot(1.0)
    assert build_snapshot(STATE, {**EXTRAS, "transmit": block})["transmit"] == block
    assert "transmit" not in build_snapshot(STATE, EXTRAS)
    assert "transmit" not in build_snapshot(STATE, {**EXTRAS, "transmit": "fc"})
