"""Contract tests for the /pair consumer (docs/oak-coordination.md section 4). No device."""
import copy
import io
import struct

import numpy as np
import pytest

import oak_pair as op
from oak_distance import estimate_person_z

SIZE = (64, 48)          # small mode keeps the tests fast; geometry rules are size-independent
W, H = SIZE
RGB = bytes(W * H * 3)


def depth(value=2500):
    return np.full((H, W), value, dtype=np.uint16)


def make(**kw):
    kw.setdefault("size", SIZE)
    return op.encode_pair(kw.pop("rgb", RGB), kw.pop("depth_mm", None), **kw)


def parse(body):
    return op.parse_bytes(body, SIZE)


def repack(meta, depth_bytes=b"", rgb=RGB):
    return op.pack(meta, rgb, depth_bytes)


# ---- accepted cases ---------------------------------------------------------------------

def test_valid_pair_round_trips_depth_values():
    d = depth()
    d[3, 5] = 0
    d[10, 20] = 65535
    body, _ = make(depth_mm=d, depth_device_ns=1_000_400_000)
    p = parse(body)
    assert p.meta["depth_valid"] is True
    assert p.depth_mm.dtype == np.uint16 and p.depth_mm.shape == (H, W)
    assert np.array_equal(p.depth_mm, d)
    assert p.meta["pair_skew_ns"] == 400_000
    assert p.meta["rgb"]["capture_device_ns"] != p.meta["depth"]["capture_device_ns"]


@pytest.mark.parametrize("reason", sorted(op.ABSENT_REASONS))
def test_rgb_only_for_each_absent_reason(reason):
    body, _ = make(reason=reason)
    p = parse(body)
    assert p.depth_mm is None and p.meta["depth_reason"] == reason
    assert len(p.rgb) == len(RGB)
    assert p.meta["depth"]["seq"] is None and p.meta["pair_skew_ns"] is None
    assert p.meta["depth"]["native_width"] is not None       # configuration stays known


def test_negative_skew_is_signed_depth_minus_rgb():
    body, _ = make(depth_mm=depth(), depth_device_ns=999_600_000)
    assert parse(body).meta["pair_skew_ns"] == -400_000


def test_parsed_depth_feeds_the_estimator():
    body, _ = op.encode_pair(bytes(1280 * 720 * 3), np.full((720, 1280), 3200, dtype=np.uint16))
    p = op.parse_bytes(body)
    r = estimate_person_z(p.depth_mm, (500, 150, 240, 520))
    assert r["status"] == "ok" and r["z_m"] == pytest.approx(3.2)


# ---- envelope breaches ------------------------------------------------------------------

def test_metadata_over_cap_is_rejected_before_reading_it():
    class Stream(io.BytesIO):
        reads = 0

        def read(self, n=-1):
            Stream.reads += 1
            return super().read(n)
    s = Stream(struct.pack(">I", op.MAX_METADATA_BYTES + 1) + b"x" * 100)
    with pytest.raises(op.PairError, match="metadata length"):
        op.read_pair(s, 10_000, SIZE)
    assert Stream.reads == 1                                  # only the 4-byte prefix


def test_zero_metadata_length_is_rejected():
    with pytest.raises(op.PairError):
        parse(struct.pack(">I", 0))


def test_non_json_metadata_is_rejected():
    with pytest.raises(op.PairError, match="JSON"):
        parse(struct.pack(">I", 4) + b"\xff\xfe{]")


@pytest.mark.parametrize("cut", [2, 40, -1, -(W * H)])
def test_truncated_body_is_rejected(cut):
    body, _ = make(depth_mm=depth())
    with pytest.raises(op.PairError):
        op.read_pair(io.BytesIO(body[:cut]), len(body), SIZE)


def test_content_length_mismatch_is_rejected():
    body, _ = make(depth_mm=depth())
    with pytest.raises(op.PairError, match="Content-Length"):
        op.read_pair(io.BytesIO(body), len(body) + 1, SIZE)


def test_trailing_bytes_are_rejected():
    body, _ = make()
    with pytest.raises(op.PairError, match="trailing"):
        op.read_pair(io.BytesIO(body + b"z"), len(body), SIZE)


class CountingStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.requested = []

    def read(self, n=-1):
        self.requested.append(n)
        return super().read(n)


def assert_rejected_without_payload_read(meta, match):
    blob = repack(meta)
    stream = CountingStream(blob)
    with pytest.raises(op.PairError, match=match):
        op.read_pair(stream, 4 + 10 ** 13, SIZE)
    assert max(stream.requested) <= op.MAX_METADATA_BYTES     # nothing payload-sized requested


def test_huge_rgb_stride_and_length_rejected_before_payload_read():
    # Review astra-0004 finding 1: stride used to have only a lower bound.
    _, meta = make()
    meta["rgb"]["stride"] = 10 ** 12
    meta["rgb"]["length"] = 10 ** 12 * H
    assert_rejected_without_payload_read(meta, "RGB stride")


def test_huge_rgb_length_rejected_before_payload_read():
    _, meta = make()
    meta["rgb"]["length"] = 10 ** 9
    assert_rejected_without_payload_read(meta, "RGB length")


def test_huge_depth_stride_and_length_rejected_before_payload_read():
    _, meta = make(depth_mm=depth())
    meta["depth"]["stride"] = 10 ** 12
    meta["depth"]["length"] = 10 ** 12 * H
    assert_rejected_without_payload_read(meta, "depth stride")


def test_padded_rows_are_not_part_of_v1():
    _, meta = make(depth_mm=depth())
    meta["depth"]["stride"] = (W + 8) * 2
    meta["depth"]["length"] = (W + 8) * 2 * H
    assert_rejected_without_payload_read(meta, "depth stride")


def test_odd_depth_stride_is_a_pair_error_not_a_numpy_error():
    _, meta = make(depth_mm=depth())
    meta["depth"]["stride"] = W * 2 + 1
    meta["depth"]["length"] = (W * 2 + 1) * H
    assert_rejected_without_payload_read(meta, "depth stride")


def test_float_lengths_are_rejected():
    _, meta = make()
    meta["rgb"]["length"] = float(W * H * 3)
    assert_rejected_without_payload_read(meta, "RGB length")


# ---- metadata breaches ------------------------------------------------------------------

def breach(mutate, valid_depth=True, match=None):
    d = depth() if valid_depth else None
    _, meta = make(depth_mm=d)
    meta = copy.deepcopy(meta)
    mutate(meta)
    payload = d.astype("<u2").tobytes() if valid_depth else b""
    with pytest.raises(op.PairError, match=match):
        parse(repack(meta, payload))


def test_wrong_schema_version():
    breach(lambda m: m.update(schema_version=2), match="schema_version")


def test_unexpected_rgb_geometry():
    breach(lambda m: m["rgb"].update(width=W + 2), match="RGB geometry")


def test_depth_not_aligned_to_rgb():
    breach(lambda m: m["depth"].update(width=W // 2), match="aligned")


def test_depth_timestamp_must_not_be_missing_when_valid():
    breach(lambda m: m["depth"].update(capture_device_ns=None), match="capture_device_ns")


def test_skew_over_declared_limit_is_rejected():
    def f(m):
        m["depth"]["capture_device_ns"] = m["rgb"]["capture_device_ns"] + 50_000_000
        m["pair_skew_ns"] = 50_000_000
    breach(f, match="exceeds")


def test_skew_must_agree_with_timestamps():
    breach(lambda m: m.update(pair_skew_ns=123), match="depth minus RGB")


def test_skew_with_the_wrong_sign_is_rejected():
    def f(m):
        m["depth"]["capture_device_ns"] = m["rgb"]["capture_device_ns"] + 400_000
        m["pair_skew_ns"] = -400_000
    breach(f, match="depth minus RGB")


@pytest.mark.parametrize("mutate,match", [
    (lambda m: m.update(calibration_id=""), "calibration_id"),
    (lambda m: m.pop("calibration_id"), "calibration_id"),
    (lambda m: m["depth"].update(native_width=0), "native_width"),
    (lambda m: m["depth"].update(native_height=10 ** 6), "native_height"),
    (lambda m: m["depth"].update(native_width=None), "native_width"),
    (lambda m: m["clock_domain"]["device"].update(id="something-else"), "device clock"),
    (lambda m: m["clock_domain"]["host"].update(boot_id=""), "boot_id"),
    (lambda m: m["rgb"].update(seq=-1), "rgb.seq"),
    (lambda m: m["rgb"].update(capture_device_ns=1.5), "rgb.capture_device_ns"),
    (lambda m: m["depth"].update(capture_host_ns=-5), "depth.capture_host_ns"),
    (lambda m: m["depth"].update(seq="7"), "depth.seq"),
    (lambda m: m.update(depth_reference_frame=""), "depth_reference_frame"),
    (lambda m: m.update(epoch=""), "epoch"),
])
def test_geometry_clock_and_type_requirements(mutate, match):
    breach(mutate, match=match)


def test_absent_depth_keeps_null_measurements_and_does_not_need_native_dims():
    _, meta = make()
    meta["depth"]["native_width"] = None
    meta["calibration_id"] = None
    assert parse(repack(meta)).depth_mm is None


def test_valid_depth_with_non_ok_reason():
    breach(lambda m: m.update(depth_reason="skew_exceeded"), match="reason")


def test_absent_depth_with_fabricated_timestamp():
    breach(lambda m: m["depth"].update(capture_device_ns=0), valid_depth=False, match="must be null")


def test_absent_depth_with_copied_skew():
    breach(lambda m: m.update(pair_skew_ns=0), valid_depth=False, match="must be null")


def test_absent_depth_with_unknown_reason():
    breach(lambda m: m.update(depth_reason="ok"), valid_depth=False, match="known reason")


def test_absent_depth_with_payload_bytes():
    breach(lambda m: m["depth"].update(length=16), valid_depth=False, match="zero length")


def test_wrong_host_clock():
    breach(lambda m: m["clock_domain"]["host"].update(id="CLOCK_REALTIME"), match="host clock")


def test_device_clock_epoch_must_match_epoch():
    breach(lambda m: m["clock_domain"]["device"].update(epoch="other"), match="epoch")


def test_missing_depth_reference_frame():
    breach(lambda m: m.pop("depth_reference_frame"), match="depth_reference_frame")


def test_boolean_is_not_accepted_as_integer():
    breach(lambda m: m.update(pair_id=True), match="pair_id")


# ---- cursor -----------------------------------------------------------------------------

def test_cursor_queries():
    c = op.PairCursor()
    assert c.query() == "/pair?depth=1"
    assert c.query(want_depth=False) == "/pair?depth=0"
    assert c.accept(parse(make(pair_id=7, epoch="A")[0])) == "first"
    assert c.query() == "/pair?after=7&epoch=A&depth=1"


def test_cursor_next_and_epoch_change():
    c = op.PairCursor()
    c.accept(parse(make(pair_id=7, epoch="A")[0]))
    assert c.accept(parse(make(pair_id=9, epoch="A")[0])) == "next"
    assert c.accept(parse(make(pair_id=0, epoch="B")[0])) == "epoch_changed"   # id restarts
    assert c.query() == "/pair?after=0&epoch=B&depth=1"


def test_cursor_rejects_repeat_or_backwards_within_epoch():
    c = op.PairCursor()
    c.accept(parse(make(pair_id=7, epoch="A")[0]))
    for bad in (7, 3):
        with pytest.raises(op.PairError, match="did not increase"):
            c.accept(parse(make(pair_id=bad, epoch="A")[0]))
    assert (c.epoch, c.pair_id) == ("A", 7)                  # state unchanged after rejection


def test_optional_geometry_bound_to_epoch_and_dimensions():
    from oak.geometry import from_transform
    class T:
        def getSize(self):return (1280,720)
        def getIntrinsicMatrix(self):return [[975,0,642],[0,974,351],[0,0,1]]
        def getDistortionCoefficients(self):return [0]*8
    # Validate geometry directly here; the parser rejects a mismatch before reading pixels.
    from oak_pair import validate_meta,PairError
    meta={'schema_version':1,'epoch':'new','pair_id':0,'geometry':from_transform(T(),'old','cal')}
    with pytest.raises(PairError,match='epoch'):validate_meta(meta,(1280,720))
