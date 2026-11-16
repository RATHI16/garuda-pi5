"""Tape-comparison scoring tests. Synthetic snapshots; no network, no device."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import oak_tape as m  # noqa: E402

INF, NAN = float("inf"), float("nan")


def snap(seq, z=2.5, status="ok", cls="person", age=100, lock=7, stereo=True, epoch="E"):
    s = {"age_ms": age, "frame": {"seq": seq, "epoch": epoch}, "lock": {"id": lock, "class": cls}}
    if stereo:
        s["stereo"] = {"status": status, "reason": status, "z_m": z if status == "ok" else None,
                       "calibration_id": "cal-1"}
        if status in ("ok", "ambiguous", "invalid"):      # the estimator ran
            s["stereo"]["estimator_config"] = "est-1"
    return s


def rec(label, which, tape, samples, rid=None, verified=False, **extra):
    return {"recording_id": rid or f"id-{which}-{label}", "label": label, "set": which,
            "tape_m": tape, "tape_reference": "test", "geometry_verified": verified,
            "geometry_evidence_id": "benchlog-geometry-1", "tape_offset_m": 0.0,
            "tape_uncertainty_m": 0.01,
            "consumer_rev": "c1", "producer_rev": "p1", "samples": samples, **extra}


def frames(start, n, z=2.5, **kw):
    return [snap(start + i, z, **kw) for i in range(n)]


# ---- comparison --------------------------------------------------------------------------

def test_unverified_geometry_gives_a_difference_not_an_accuracy_figure():
    p = m.score_recording(rec("p250", "validate", 2.50, [snap(i, 2.56 + 0.01 * (i % 3)) for i in range(30)]))
    assert p["unverified_difference_m"] == pytest.approx(0.07)
    assert p["unverified_difference_percent"] == pytest.approx(2.8)
    assert "z_minus_tape_m" not in p and p["geometry_verified"] is False
    r = m.score([rec("p250", "validate", 2.50, frames(0, 5, 2.6))], "validate")
    assert "not depth accuracy" in r["statement"]
    assert "largest_abs_z_minus_tape_m" not in r


def test_verified_geometry_reports_z_minus_tape():
    p = m.score_recording(rec("p", "validate", 2.5, frames(0, 9, 2.6), verified=True))
    assert p["z_minus_tape_m"] == pytest.approx(0.1) and "unverified_difference_m" not in p


@pytest.mark.parametrize("flag", ["yes", 1, "true", None])
def test_geometry_verified_must_be_literally_true(flag):
    p = m.score_recording(rec("p", "validate", 2.5, frames(0, 3), verified=flag))
    assert p["geometry_verified"] is False and "z_minus_tape_m" not in p


# ---- R2: outages, identities, mixed targets ----------------------------------------------

def test_outage_lowers_availability_instead_of_vanishing():
    samples = [snap(1)] + [{"error": "URLError", "t_s": float(i)} for i in range(19)]
    p = m.score_recording(rec("p", "validate", 2.5, samples))
    assert p["attempts"] == 20 and p["request_failures"] == 19 and p["successful_responses"] == 1
    assert p["ok_share_of_distinct_usable_frames"] == 1.0
    assert p["successful_request_share_of_attempts"] == pytest.approx(0.05)
    assert p["fresh_ok_response_share_of_attempts"] == pytest.approx(0.05)


@pytest.mark.parametrize("frame", [{}, {"seq": 1}, {"epoch": "E"}, {"seq": None, "epoch": None},
                                   {"seq": "4", "epoch": "E"}, {"seq": True, "epoch": "E"},
                                   {"seq": -1, "epoch": "E"}, {"seq": 2, "epoch": ""}, None])
def test_missing_or_invalid_frame_identity_is_excluded_not_pooled(frame):
    s1, s2 = snap(1), snap(2)
    s1["frame"], s2["frame"] = frame, frame
    p = m.score_recording(rec("p", "validate", 2.5, [s1, s2, snap(3)]))
    assert p["excluded"] == {"invalid_frame_identity": 2} and p["distinct_usable_frames"] == 1


def test_two_targets_in_one_recording_are_not_compared_to_one_tape_reading():
    samples = frames(0, 5, 2.5, lock=7) + frames(5, 5, 4.0, lock=9)
    p = m.score_recording(rec("p", "validate", 2.5, samples))
    assert p["mixed"] is True and p["lock_ids_seen"] == [7, 9]
    assert "unverified_difference_m" not in p and "z_minus_tape_m" not in p
    assert m.score([rec("p", "validate", 2.5, samples)], "validate")["mixed_recordings"] == ["p"]


def test_camera_restart_inside_a_recording_marks_it_mixed():
    p = m.score_recording(rec("p", "validate", 2.5, frames(0, 3) + frames(0, 3, epoch="F")))
    assert p["camera_epochs_seen"] == 2 and p["mixed"] is True


def test_repeated_frames_are_not_counted_twice():
    p = m.score_recording(rec("p", "validate", 2.5, [snap(1), snap(1), snap(1), snap(2)]))
    assert p["distinct_usable_frames"] == 2 and p["excluded"] == {"repeated_frame": 2}


def test_exclusions_are_counted_by_reason():
    samples = [snap(1), snap(2, cls="chair"), snap(3, age=2000), snap(4, lock=None),
               snap(5, stereo=False), snap(6, age=None), snap(7, age=INF), snap(8, age=True)]
    p = m.score_recording(rec("p", "validate", 2.5, samples))
    assert p["ok_frames"] == 1
    assert p["excluded"] == {"locked_class_not_person": 1, "stale_state": 4, "no_lock": 1,
                             "no_stereo_block": 1}


# ---- R4: numeric validity ----------------------------------------------------------------

@pytest.mark.parametrize("z", [INF, -INF, NAN, True, 0, -2.5, "2.5", None])
def test_invalid_ok_depth_is_rejected_and_counted(z):
    samples = [snap(1, 2.5), snap(2, 2.5), snap(3, 2.5)]
    samples[1]["stereo"]["z_m"] = z
    p = m.score_recording(rec("p", "validate", 2.5, samples))
    assert p["ok_frames"] == 2 and p["ok_with_invalid_value"] == 1
    assert p["z_m"]["median"] == 2.5 and p["unverified_difference_m"] == 0.0


@pytest.mark.parametrize("tape", [INF, NAN, True, 0, -1.0, "2.5"])
def test_invalid_tape_is_never_scored(tape):
    r = m.score([rec("p", "validate", tape, frames(0, 3))], "validate")
    assert r["recordings"] == [] and len(r["unlabelled"]) == 1
    p = r["unlabelled"][0]
    assert "unverified_difference_m" not in p and "tape_rejected" in p


def test_unknown_tape_is_kept_unlabelled_without_complaint():
    r = m.score([rec("p", "validate", None, frames(0, 3))], "validate")
    assert len(r["unlabelled"]) == 1 and "tape_rejected" not in r["unlabelled"][0]


def test_recording_with_no_value_is_listed():
    r = m.score([rec("far", "validate", 6.0, frames(0, 5, status="invalid"))], "validate")
    assert r["recordings_without_any_value"] == ["far"]
    assert "unverified_difference_m" not in r["recordings"][0]


# ---- R3: held-out enforcement ------------------------------------------------------------

def test_relabelled_copy_of_tuning_data_is_refused():
    tune = rec("a", "tune", 2.5, frames(0, 10))
    copy_ = copy.deepcopy(tune)
    copy_.update(label="b", set="validate", recording_id="new-id")
    r = m.score([tune, copy_], "validate")
    assert "refused" in r and r["recordings"] == []
    assert any("identical" in p for p in r["refused"])


def test_same_recording_id_is_refused():
    r = m.score([rec("a", "tune", 2.5, frames(0, 5), rid="X"),
                 rec("b", "validate", 2.5, frames(100, 5), rid="X")], "validate")
    assert any("recording id" in p for p in r["refused"])


def test_overlapping_frame_identities_are_refused_even_if_content_differs():
    tune = rec("a", "tune", 2.5, frames(0, 10))
    val = rec("b", "validate", 2.5, frames(8, 10, 2.6))      # frames 8 and 9 overlap
    r = m.score([tune, val], "validate")
    assert any("2 frame identities" in p for p in r["refused"])


def test_same_position_recorded_independently_is_allowed():
    tune = rec("p250", "tune", 2.5, frames(0, 10, epoch="E1"))
    val = rec("p250", "validate", 2.5, frames(0, 10, 2.6, epoch="E2"))   # same label, separate recording
    r = m.score([tune, val], "validate")
    assert "refused" not in r and len(r["recordings"]) == 1


def test_missing_recording_id_or_revisions_are_refused():
    v = rec("b", "validate", 2.5, frames(0, 5))
    v["recording_id"] = None
    assert any("no recording id" in p for p in m.score([v], "validate")["refused"])
    v = rec("b", "validate", 2.5, frames(0, 5), consumer_rev="")
    assert any("consumer_rev missing" in p for p in m.score([v], "validate")["refused"])


def test_validation_set_spanning_revisions_is_refused():
    r = m.score([rec("a", "validate", 2.5, frames(0, 5)),
                 rec("b", "validate", 3.0, frames(50, 5), producer_rev="p2")], "validate")
    assert any("several producer_rev" in p for p in r["refused"])


def test_tuning_set_scores_without_the_held_out_check():
    tune = rec("a", "tune", 2.5, frames(0, 10))
    dup = copy.deepcopy(tune)
    dup.update(label="b", set="validate")
    assert "refused" not in m.score([tune, dup], "tune")


def test_scope_statement_is_always_present():
    assert "cannot certify" in m.score([], "validate")["held_out_scope"]


def test_unknown_set_is_an_error_and_unknown_set_recordings_are_listed():
    with pytest.raises(ValueError):
        m.score([], "all")
    r = m.score([rec("x", "misc", 2.5, frames(0, 2))], "tune")
    assert r["recordings_with_unknown_set"] == ["x"] and r["recordings"] == []


# ---- collect argument validation ---------------------------------------------------------

BASE = dict(base="http://127.0.0.1:9", which="validate", label="p", seconds=0.01, interval=0.01,
            consumer_rev="c", producer_rev="p")


@pytest.mark.parametrize("override", [
    {"seconds": 0}, {"seconds": -1}, {"seconds": INF}, {"seconds": NAN}, {"interval": 0},
    {"interval": INF}, {"tape_m": INF, "tape_reference": "r"}, {"tape_m": NAN, "tape_reference": "r"},
    {"tape_m": 0, "tape_reference": "r"}, {"tape_m": -2, "tape_reference": "r"},
    {"tape_m": 2.5, "tape_reference": "  "}, {"consumer_rev": ""}, {"producer_rev": ""},
    {"which": "all"},
])
def test_collect_rejects_malformed_arguments(tmp_path, override):
    with pytest.raises(ValueError):
        m.collect(out_dir=tmp_path, **{**BASE, **override})
    assert list(tmp_path.iterdir()) == []


def test_collect_records_failed_attempts_and_refuses_to_overwrite(tmp_path):
    path, summary = m.collect(out_dir=tmp_path, **BASE)       # nothing listens on port 9
    assert summary["attempts"] >= 1 and summary["request_failures"] == summary["attempts"]
    assert summary["successful_request_share_of_attempts"] == 0.0 and summary["recording_id"]
    with pytest.raises(FileExistsError):
        m.collect(out_dir=tmp_path, **BASE)


# ---- review astra-0014 -------------------------------------------------------------------

def test_availability_does_not_depend_on_polling_rate():
    samples = [snap(1) for _ in range(10)] + [{"error": "URLError"} for _ in range(10)]
    p = m.score_recording(rec("p", "validate", 2.5, samples))
    assert p["successful_request_share_of_attempts"] == 0.5
    assert p["fresh_ok_response_share_of_attempts"] == 0.5
    assert p["distinct_usable_frames"] == 1 and p["ok_frames"] == 1
    assert p["new_ok_frames_per_attempt"] == pytest.approx(0.05)
    assert not any("time" in k or "availability" in k for k in p)


def test_fresh_ok_share_excludes_stale_non_person_and_non_ok_responses():
    samples = [snap(1), snap(2, age=5000), snap(3, cls="chair"), snap(4, status="invalid")]
    p = m.score_recording(rec("p", "validate", 2.5, samples))
    assert p["successful_request_share_of_attempts"] == 1.0
    assert p["fresh_ok_response_share_of_attempts"] == 0.25


def test_two_calibrations_or_estimator_configs_in_one_recording_are_mixed():
    a, b = frames(0, 3), frames(3, 3)
    for s in b:
        s["stereo"]["calibration_id"] = "cal-2"
    p = m.score_recording(rec("p", "validate", 2.5, a + b))
    assert p["mixed"] and p["mixed_reasons"] == ["several calibration ids"]
    assert "unverified_difference_m" not in p
    c = frames(3, 3)
    for s in c:
        s["stereo"]["estimator_config"] = "est-2"
    p = m.score_recording(rec("p", "validate", 2.5, a + c))
    assert p["mixed_reasons"] == ["several estimator configurations"]


def test_validation_set_spanning_calibrations_or_estimator_configs_is_refused():
    other = frames(50, 5, epoch="F")
    for s in other:
        s["stereo"]["calibration_id"] = "cal-2"
    r = m.score([rec("a", "validate", 2.5, frames(0, 5)), rec("b", "validate", 3.0, other)], "validate")
    assert any("several calibration ids" in p for p in r["refused"])
    other = frames(50, 5, epoch="F")
    for s in other:
        s["stereo"]["estimator_config"] = "est-2"
    r = m.score([rec("a", "validate", 2.5, frames(0, 5)), rec("b", "validate", 3.0, other)], "validate")
    assert any("several estimator configurations" in p for p in r["refused"])


def test_validation_sample_without_calibration_id_is_refused():
    samples = frames(0, 5)
    del samples[2]["stereo"]["calibration_id"]
    r = m.score([rec("a", "validate", 2.5, samples)], "validate")
    assert any("no calibration id" in p for p in r["refused"])


@pytest.mark.parametrize("missing", [{"geometry_evidence_id": ""}, {"geometry_evidence_id": None},
                                     {"tape_uncertainty_m": None}, {"tape_uncertainty_m": -0.1},
                                     {"tape_uncertainty_m": INF}, {"tape_offset_m": None},
                                     {"tape_offset_m": NAN}])
def test_verified_flag_without_named_evidence_is_treated_as_unverified(missing):
    p = m.score_recording(rec("p", "validate", 2.5, frames(0, 5, 2.6), verified=True, **missing))
    assert p["geometry_verified"] is False and "geometry_claim_incomplete" in p
    assert "z_minus_tape_m" not in p and "unverified_difference_m" in p


def test_tape_offset_is_applied_only_to_verified_comparisons():
    p = m.score_recording(rec("p", "validate", 2.5, frames(0, 5, 2.6), verified=True, tape_offset_m=0.04))
    assert p["z_minus_tape_m"] == pytest.approx(0.06) and p["tape_offset_m"] == 0.04
    p = m.score_recording(rec("p", "validate", 2.5, frames(0, 5, 2.6), tape_offset_m=0.04))
    assert p["unverified_difference_m"] == pytest.approx(0.1)


@pytest.mark.parametrize("override", [
    {"geometry_verified": True, "tape_m": 2.5, "tape_reference": "r"},
    {"geometry_verified": True, "tape_m": 2.5, "tape_reference": "r", "geometry_evidence_id": "e",
     "tape_offset_m": 0.0},
    {"geometry_verified": True, "geometry_evidence_id": "e", "tape_offset_m": 0.0, "tape_uncertainty_m": 0.01},
    {"tape_offset_m": INF}, {"tape_uncertainty_m": -1.0}, {"tape_uncertainty_m": NAN},
])
def test_collect_rejects_incomplete_geometry_claims(tmp_path, override):
    with pytest.raises(ValueError):
        m.collect(out_dir=tmp_path, **{**BASE, **override})
    assert list(tmp_path.iterdir()) == []


# ---- review astra-0015 -------------------------------------------------------------------

def test_verified_percentage_uses_the_corrected_reference_and_exact_keys():
    p = m.score_recording(rec("p", "validate", 1.8, frames(0, 5, 2.2), verified=True, tape_offset_m=0.2))
    assert p["comparison_reference_m"] == pytest.approx(2.0)
    assert p["z_minus_tape_m"] == pytest.approx(0.2)
    assert p["z_minus_tape_percent"] == pytest.approx(10.0)
    comparison_keys = {k for k in p if "minus" in k or "difference" in k or "percent" in k}
    assert comparison_keys == {"z_minus_tape_m", "z_minus_tape_percent"}


def test_unverified_percentage_uses_the_raw_tape_and_exact_keys():
    p = m.score_recording(rec("p", "validate", 1.8, frames(0, 5, 2.2), tape_offset_m=0.2))
    assert p["comparison_reference_m"] == pytest.approx(1.8)
    assert p["unverified_difference_m"] == pytest.approx(0.4)
    assert p["unverified_difference_percent"] == pytest.approx(100 * 0.4 / 1.8)
    comparison_keys = {k for k in p if "minus" in k or "difference" in k or "percent" in k}
    assert comparison_keys == {"unverified_difference_m", "unverified_difference_percent"}


@pytest.mark.parametrize("offset", [-2.5, -3.0])
def test_non_positive_corrected_reference_is_rejected_and_shown(offset):
    p = m.score_recording(rec("p", "validate", 2.5, frames(0, 5, 2.6), verified=True, tape_offset_m=offset))
    assert "z_minus_tape_m" not in p and "z_minus_tape_percent" not in p
    assert p["comparison_reference_m"] == pytest.approx(2.5 + offset) and "comparison_rejected" in p


@pytest.mark.parametrize("status", ["ambiguous", "invalid"])
def test_estimator_config_on_rejected_results_is_pinned_too(status):
    good = frames(0, 3)
    other = snap(3, status=status)
    other["stereo"]["estimator_config"] = "est-B"
    p = m.score_recording(rec("p", "validate", 2.5, good + [other]))
    assert p["estimator_configs"] == ["est-1", "est-B"]
    assert p["mixed_reasons"] == ["several estimator configurations"]
    r = m.score([rec("p", "validate", 2.5, good + [other])], "validate")
    assert any("several estimator configurations" in x for x in r["refused"])


def test_rejected_result_without_estimator_config_is_refused_for_validation():
    other = snap(3, status="invalid")
    del other["stereo"]["estimator_config"]                    # estimator ran but no identity
    r = m.score([rec("p", "validate", 2.5, frames(0, 3) + [other])], "validate")
    assert any("no estimator configuration" in x for x in r["refused"])


def test_unavailable_rows_may_omit_estimator_config():
    samples = frames(0, 3) + [snap(3, status="unavailable")]
    p = m.score_recording(rec("p", "validate", 2.5, samples))
    assert p["estimator_configs"] == ["est-1"] and not p["mixed"]
    assert "refused" not in m.score([rec("p", "validate", 2.5, samples)], "validate")


# ---- fit: offset and scale from tuning recordings ----------------------------------------

def tune_set(pairs, **kw):
    return [rec(f"t{i}", "tune", tape, frames(100 * i, 6, z, epoch=f"E{i}"), **kw)
            for i, (tape, z) in enumerate(pairs)]


def test_fit_recovers_offset_and_scale():
    # z = 1.02 * tape + 0.05
    r = m.fit(tune_set([(t, 1.02 * t + 0.05) for t in (1.0, 2.0, 3.0, 4.0, 5.0)]))
    assert r["empirical_slope"] == pytest.approx(1.02) and r["empirical_intercept_m"] == pytest.approx(0.05)
    assert r["residual_rms_m"] == pytest.approx(0.0, abs=1e-9)
    assert r["tape_range_m"] == [1.0, 5.0] and len(r["used"]) == 5


def test_fit_reports_residuals_without_judging_them():
    r = m.fit(tune_set([(1.0, 1.0), (2.0, 2.3), (3.0, 3.0), (4.0, 4.0)]))
    assert r["largest_abs_residual_m"] > 0.1
    assert all("residual_m" in u for u in r["used"])
    assert not any(k in r for k in ("pass", "ok", "accepted"))


def test_fit_needs_four_distinct_distances():
    r = m.fit(tune_set([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]))
    assert "refused" in r and "empirical_slope" not in r
    r = m.fit(tune_set([(1.0, 1.0), (1.0, 1.1), (2.0, 2.0), (3.0, 3.0)]))   # a repeat is not a fourth
    assert "refused" in r


def test_fit_ignores_validation_recordings():
    records = tune_set([(t, t + 0.1) for t in (1.0, 2.0, 3.0, 4.0)])
    records.append(rec("v", "validate", 2.5, frames(900, 6, 9.9, epoch="V")))
    r = m.fit(records)
    assert r["empirical_intercept_m"] == pytest.approx(0.1) and all(u["label"] != "v" for u in r["used"])


def test_fit_skips_unlabelled_mixed_and_valueless_recordings_and_says_why():
    records = tune_set([(t, t) for t in (1.0, 2.0, 3.0, 4.0)])
    records.append(rec("nolabel", "tune", None, frames(500, 3, epoch="N")))
    records.append(rec("mixed", "tune", 2.0, frames(600, 3, lock=7, epoch="M") + frames(603, 3, lock=9, epoch="M")))
    records.append(rec("novalue", "tune", 2.0, frames(700, 3, status="invalid", epoch="X")))
    r = m.fit(records)
    assert {s_["label"]: s_["reason"].split(":")[0] for s_ in r["skipped"]} == \
        {"nolabel": "no tape value", "mixed": "mixed", "novalue": "no ok value"}
    assert r["empirical_slope"] == pytest.approx(1.0)


def test_fit_refuses_mixed_tape_references_or_calibrations():
    records = tune_set([(t, t) for t in (1.0, 2.0, 3.0, 4.0)])
    records[2]["tape_reference"] = "from the tripod leg"
    assert any("tape references" in p for p in m.fit(records)["refused"])
    records = tune_set([(t, t) for t in (1.0, 2.0, 3.0, 4.0)])
    for s_ in records[1]["samples"]:
        s_["stereo"]["calibration_id"] = "cal-2"
    assert any("calibration ids" in p for p in m.fit(records)["refused"])


# ---- fit interpretation and provenance (review astra-0022 R4) ----------------------------

def test_fit_names_its_outputs_as_empirical_and_states_what_it_is_not():
    r = m.fit(tune_set([(t, 1.1 * (t + 0.2)) for t in (1.0, 2.0, 3.0, 4.0)]))   # z = a*(t + delta)
    assert r["empirical_slope"] == pytest.approx(1.1)
    assert r["empirical_intercept_m"] == pytest.approx(1.1 * 0.2)               # a*delta, not delta
    assert "not a camera-origin measurement" in r["interpretation"]
    for forbidden in ("intercept_m", "slope", "tape_offset_m", "tape_uncertainty_m",
                      "geometry_verified", "origin_offset_m", "uncertainty_m"):
        assert forbidden not in r


def test_fit_with_complete_agreeing_provenance_is_not_exploratory_and_reports_it():
    r = m.fit(tune_set([(t, t) for t in (1.0, 2.0, 3.0, 4.0)]))
    assert r["exploratory"] is False
    assert r["provenance"] == {"calibration_ids": ["cal-1"], "estimator_configs": ["est-1"],
                               "tape_reference": "test", "consumer_rev": "c1", "producer_rev": "p1"}
    assert all(u["recording_id"] for u in r["used"])


@pytest.mark.parametrize("strip", ["calibration_id", "estimator_config", "tape_reference",
                                   "consumer_rev", "producer_rev"])
def test_fit_with_missing_provenance_everywhere_is_marked_exploratory(strip):
    records = tune_set([(t, t) for t in (1.0, 2.0, 3.0, 4.0)])
    for rec_ in records:
        if strip in ("calibration_id", "estimator_config"):
            for s_ in rec_["samples"]:
                s_["stereo"].pop(strip, None)
        else:
            rec_[strip] = ""
    r = m.fit(records)
    assert r["exploratory"] is True and r["exploratory_because"] and "provenance" not in r
    assert r["empirical_slope"] == pytest.approx(1.0)        # still computed, clearly labelled


def test_fit_refuses_tuning_sets_that_span_revisions():
    records = tune_set([(t, t) for t in (1.0, 2.0, 3.0, 4.0)])
    records[3]["producer_rev"] = "p2"
    assert any("producer revisions" in p for p in m.fit(records)["refused"])


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_fit_treats_blank_identifiers_inside_lists_as_missing(blank):
    records = tune_set([(t, t) for t in (1.0, 2.0, 3.0, 4.0)])
    for rec_ in records:
        for s_ in rec_["samples"]:
            s_["stereo"]["calibration_id"] = blank
    r = m.fit(records)
    assert r["exploratory"] is True and "missing calibration id" in r["exploratory_because"]
