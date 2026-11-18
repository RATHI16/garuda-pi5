"""The detector's real loop() with the Hailo library stubbed (astra-0058 finding 1).

Box-only stereo (GARUDA_MONO_OAK_BOX=1, OAK_PAIR off) must follow the same camera-restart
rules as the pair path: a result computed on the old run's frame is not published once a
new run's frame exists, and the next processed frame starts from a clean tracker, lock,
gesture and cached-distance state."""
import importlib.util
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
TRACK = HERE.parents[1] / "services" / "garuda-track"


class _Any:
    def __getattr__(self, name):
        return _Any()

    def __call__(self, *a, **k):
        return _Any()


class Det:
    def __init__(self, x=500, y=100, w=240, h=560, label="person", conf=0.9):
        self.x, self.y, self.w, self.h, self.label, self.conf = x, y, w, h, label, conf


class FakeDetector:
    """infer() calls can be held open, one hold per call in order, so that a newer frame
    arrives while one is 'in flight' and so that the next call cannot overwrite the state
    before the test has looked at what the previous one published."""
    def __init__(self):
        self.holds = []                       # threading.Event per upcoming call
        self.empty = False                    # True: the person is not detected
        self.calls = 0
        self.cv = threading.Condition()

    def infer(self, img):
        with self.cv:
            self.calls += 1
            hold = self.holds.pop(0) if self.holds else None
            self.cv.notify_all()
        if hold is not None:
            hold.wait(5)
        return [] if self.empty else [Det()]

    def wait_calls(self, n, timeout=3.0):
        with self.cv:
            return self.cv.wait_for(lambda: self.calls >= n, timeout)


class FakeBox:
    def __init__(self):
        self.counters = {"requests": 0}
        self.calls = []

    def measure(self, key, capture_ns, box, kpts=None, coasting=False, target_class="person", params=None):
        self.calls.append((key, box is not None))
        self.counters["requests"] += 1
        return {"status": "ok" if box is not None else "unavailable", "reason": "ok" if box is not None else "no_target",
                "z_m": 1.5 if box is not None else None, "pair": {"epoch": key[0], "rgb_seq": key[1]}}


@pytest.fixture(scope="module")
def mono():
    env = dict(GARUDA_MONO_CAM="oak", GARUDA_MONO_OAK_BOX="1", GARUDA_MONO_OAK_PAIR="0", GARUDA_MONO_SKIP_DEPTH="1")
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    stub = types.ModuleType("hailo_platform")
    stub.__getattr__ = lambda name: _Any()
    saved = sys.modules.get("hailo_platform")
    sys.modules["hailo_platform"] = stub
    sys.path.insert(0, str(TRACK))
    try:
        spec = importlib.util.spec_from_file_location("garuda_mono_box_under_test", HERE / "garuda_mono.py")
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        if saved is None:
            sys.modules.pop("hailo_platform", None)
        else:
            sys.modules["hailo_platform"] = saved
    assert g.OAK_BOX and not g.OAK_PAIR
    from oak_stereo_state import PairDepthStore
    g._oak.update(store=PairDepthStore(), box=FakeBox())      # what capture_thread sets up in box mode
    g._viewers["n"] = 1
    g.det_under_test = FakeDetector()
    threading.Thread(target=g.loop, args=(g.det_under_test, {}), daemon=True).start()
    return g


CLOCK = {"t": None}


def push(g, epoch, n, dt=1 / 15):
    """One frame, captured dt seconds after the previous one on the test's own capture clock."""
    CLOCK["t"] = time.monotonic() if CLOCK["t"] is None else CLOCK["t"] + dt
    with g._lock:
        seq = g._frame["seq"] + 1
        g._frame.update(img=np.zeros((720, 1280, 3), np.uint8), seq=seq, t=CLOCK["t"], source="oak-rgb",
                        epoch="oak-rgb:" + epoch, capture_ns=time.monotonic_ns(), received_ns=time.monotonic_ns(),
                        pair_key=(epoch, n))
    g._frame_ready.set()
    return seq


def published(g, seq, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with g._lock:
            if g._state.get("frame_seq") == seq:
                return True
        time.sleep(0.005)
    return False


def test_box_mode_camera_restart_rejects_the_old_result_and_resets_identity(mono):
    g, det = mono, mono.det_under_test
    # Run A: the person becomes a confirmed track and is selected.
    for n in range(4):
        assert published(g, push(g, "A", n))
    with g._lock:
        tracks = list(g._state["tracks_full"])
    assert tracks, "the tracker should have a confirmed person by now"
    tid = int(tracks[0][0])
    with g._lock:
        g._lockid.update(id=tid, pinned=True)
        g._ema["raw"] = 1.23
        g._track_gestures[999999] = g.GestureFSM()     # stale per-track gesture state from run A
        g._oak["store"]._items[("A", 0)] = (None, {})
    assert published(g, push(g, "A", 4))
    with g._lock:
        assert g._state["lock_id"] == tid and g._state["stereo"]["status"] == "ok"
        last_a = g._state["frame_seq"]

    # An inference on run A is in flight when the first frame of run B arrives. The run-B
    # inference is held too, so nothing can overwrite whatever the run-A iteration published
    # before it is inspected (astra-0059 finding 2: a transient publication must be caught).
    hold_a, hold_b = threading.Event(), threading.Event()
    before = det.calls
    det.holds = [hold_a, hold_b]
    try:
        in_flight = push(g, "A", 5)
        assert det.wait_calls(before + 1)
        new_run = push(g, "B", 0)
        hold_a.set()
        assert det.wait_calls(before + 2), "the loop must move on to the new run's frame"
        with g._lock:                          # run-A iteration has finished; run-B inference is still held
            after_old = (g._state.get("frame_seq"), g._state.get("frame_epoch"))
            lock_after_old = dict(g._lockid)
        assert after_old[0] == last_a and after_old[0] != in_flight, "the old run's result was published"
        assert after_old[1].startswith("oak-rgb:A:")
        assert (lock_after_old["id"], lock_after_old["pinned"]) == (None, False), "the reset happens on entering the new run's frame"
    finally:                                   # never leave the shared loop thread held for later tests
        det.holds = []
        hold_a.set()
        hold_b.set()

    assert published(g, new_run), "the new run's frame must be processed and published"
    with g._lock:
        state = dict(g._state)
        lock = dict(g._lockid)
        ema = g._ema["raw"]
        gestures = dict(g._track_gestures)
        stored = len(g._oak["store"])
    assert in_flight not in (last_a, new_run)
    assert not published(g, in_flight, timeout=0.3), "a result from the old run must never be published"
    assert state["frame_epoch"].startswith("oak-rgb:B:")
    assert (lock["id"], lock["pinned"]) == (None, False) and state["lock_id"] is None
    assert state["tracks_full"] == [] and state["count"] == 0      # fresh tracker: nothing confirmed on its first frame
    assert state["stereo"]["status"] == "unavailable" and state["stereo"]["reason"] == "no_target"
    assert ema is None and gestures == {} and stored == 0
    # The old run's identity cannot be re-selected by a late request either.
    from mono_state import select_by_id
    with g._lock:
        ok, body = select_by_id(g._state, g.INSTANCE, {"id": [str(tid)], "epoch": ["oak-rgb:A:0"], "instance": [g.INSTANCE]}, time.monotonic())
    assert not ok and body["reason"] == "frame_epoch_changed"


def test_box_mode_keeps_working_within_one_run(mono):
    g = mono
    seqs = [push(g, "B", n) for n in range(1, 2)]
    assert published(g, seqs[-1])
    for n in range(2, 5):
        assert published(g, push(g, "B", n))
    with g._lock:
        assert g._state["tracks_full"], "tracking resumes on the new run"


def test_stereo_off_does_not_import_the_stereo_modules():
    code = ("import sys,types\n"
            "class A:\n    def __getattr__(s,n):return A()\n    def __call__(s,*a,**k):return A()\n"
            "m=types.ModuleType('hailo_platform');m.__getattr__=lambda n:A();sys.modules['hailo_platform']=m\n"
            f"sys.path[:0]=[{str(HERE)!r},{str(TRACK)!r}]\n"
            "import garuda_mono as g\n"
            "assert not g.OAK_BOX and not g.OAK_PAIR\n"
            "assert not [n for n in ('oak_stereo_state','oak_box','oak_distance','oak_pair') if n in sys.modules],sys.modules.keys()\n"
            "print('ok')\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GARUDA_MONO_")}
    env.update(PYTHONDONTWRITEBYTECODE="1", GARUDA_MONO_CAM="csi")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr[-800:]


def test_box_mode_publishes_the_pair_identity_of_the_processed_frame(mono):
    # astra-0061 blocker 1: consumers require frame.capture_pair == stereo.pair in box mode too.
    g = mono
    assert published(g, push(g, "B", 40))
    from mono_state import build_snapshot
    with g._lock:
        state = dict(g._state)
    assert state["pair_key"] == ("B", 40)
    snap = build_snapshot(state, {"age_ms": 10})
    assert snap["frame"]["capture_pair"] == {"epoch": "B", "rgb_seq": 40}


def ids(g):
    with g._lock:
        return [int(t[0]) for t in g._state["tracks_full"] if t[1] == "person" and not t[7]]


def test_a_track_unmatched_for_too_long_in_wall_time_is_not_reattached(mono):
    """R3: the frame-count coast window (25 frames) stretches when frames stall. A person
    missing for longer than MAX_LOST_S of capture time must come back under a new ID."""
    g, det = mono, mono.det_under_test
    assert g.MAX_LOST_S == pytest.approx(g.MAX_LOST / 15.0)
    for n in range(50, 54):
        assert published(g, push(g, "B", n))
    before = ids(g)
    assert len(before) == 1
    # Short miss at the nominal rate: three frames without a detection, then back -> same ID.
    det.empty = True
    for n in range(54, 57):
        assert published(g, push(g, "B", n))
    det.empty = False
    assert published(g, push(g, "B", 57))
    assert ids(g) == before
    # Stall: only two frames pass, but 2 s of capture time (longer than MAX_LOST_S) -> new ID.
    det.empty = True
    assert published(g, push(g, "B", 58, dt=1.0))
    assert published(g, push(g, "B", 59, dt=1.0))
    det.empty = False
    for n in range(60, 64):
        assert published(g, push(g, "B", n))
    after = ids(g)
    assert len(after) == 1 and after != before, "the old ID was re-attached after a stall"


# ---- identity decisions are published only with their own frame (astra-0062 finding 2) -------
import copy as _copy
import json as _json
from urllib.request import urlopen as _urlopen


class FakeReid:
    last_timing = dict(crops=1, total_ns=1_000_000, run_ns=500_000)

    def embed(self, img, boxes):
        return [np.ones(8, np.float32) for _ in boxes]


class ScriptedOwner:
    """Stands in for the identity owner so the test controls the decision. Copyable, like the real one."""
    def __init__(self, tid=None):
        self.tid, self.selection_gen, self.rebind_to = tid, None, None

    def select(self, tid, now):
        self.tid, self.rebind_to = tid, None

    def update(self, now, people):
        old = self.tid
        if self.rebind_to is not None:
            self.tid, self.rebind_to = self.rebind_to, None
            return dict(target=self.tid, confirmed=True, state="tracking", reason="", gallery=1, binds=1, candidate=None,
                        rebound_from=old, rebound_score=0.9)
        return self.summary(now)

    def summary(self, now):
        return dict(target=self.tid, confirmed=self.tid is not None, state="tracking" if self.tid is not None else "idle",
                    reason="", gallery=1, binds=0, candidate=None, rebound_from=None, rebound_score=None)


@pytest.fixture
def identity(mono):
    g = mono
    server = g.ThreadingHTTPServer(("127.0.0.1", 0), g.make_handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    hold = {"event": None, "entered": threading.Event()}
    box = g._oak["box"]
    plain = box.measure

    def measure(*a, **k):
        hold["entered"].set()
        if hold["event"] is not None:
            hold["event"].wait(5)
        return plain(*a, **k)
    box.measure = measure
    g._reid["model"] = FakeReid()
    for n in range(100, 104):
        assert published(g, push(g, "B", n))
    tid = ids(g)[0]
    with g._lock:
        g._operator_select(tid, True)
        gen = g._lockid["gen"]
    owner = ScriptedOwner()
    owner.select(tid, 0.0)
    owner.selection_gen = gen
    g._reid["owner"] = owner
    assert published(g, push(g, "B", 104))

    def api():
        with _urlopen(f"http://127.0.0.1:{server.server_port}/api/state", timeout=2) as r:
            return _json.load(r)
    try:
        yield g, tid, hold, api
    finally:
        if hold["event"] is not None:
            hold["event"].set()
        box.measure = plain
        g._reid["model"] = None
        g._reid["owner"] = None
        with g._lock:
            g._operator_select(None, False)
            g._state.update(identity=None)
        server.shutdown()
        server.server_close()
        assert published(g, push(g, "B", 190))


def consistent(snap):
    """The identity block, when present, belongs to exactly the frame and selection shown."""
    d = snap["stats"].get("identity")
    if d is None:
        return True
    return d["frame_seq"] == snap["frame"]["seq"] and d["frame_epoch"] == snap["frame"]["epoch"] and d["target"] == snap["lock"]["id"]


def test_a_rebind_is_not_visible_before_its_frame_is_published(identity):
    g, tid, hold, api = identity
    before = api()
    assert consistent(before) and before["lock"]["id"] == tid and before["stats"]["identity"]["target"] == tid
    # The real state says what this camera offers (box-stereo OAK here): pages and the lab gate on it.
    assert before["stats"]["capabilities"] == {"camera": "oak", "distance_source": "stereo", "mono_engine": None,
                                             "depth_map": True, "cloud": True, "mount": "fixed",
                                             "mount_locked": False, "onboard_recording": False}
    assert "onboard" not in before                        # fixed camera: no vehicle command is published
    g._reid["owner"].rebind_to = 99                       # the next frame's decision moves the selection to track 99
    hold["event"] = threading.Event()
    hold["entered"].clear()
    in_flight = push(g, "B", 105)
    assert hold["entered"].wait(3)                        # decided, but the frame is not published yet
    during = api()
    with g._lock:
        lock_now = dict(g._lockid)
    assert during["frame"]["seq"] == before["frame"]["seq"] != in_flight
    assert consistent(during) and during["stats"]["identity"]["target"] == tid and during["stats"]["identity"]["rebound_from"] is None
    assert lock_now["id"] == tid, "the selection moved before its frame was published"
    hold["event"].set()
    assert published(g, in_flight)
    after = api()
    assert consistent(after) and after["stats"]["identity"]["rebound_from"] == tid and after["lock"]["id"] == 99
    assert after["stats"]["identity"]["instance"] == g.INSTANCE and after["stats"]["identity"]["selection_gen"] == lock_now["gen"]
    with g._lock:
        assert g._lockid["id"] == 99 and g._lockid["gen"] == lock_now["gen"]     # the owner's move is not an operator selection


def test_an_operator_selection_during_inference_is_not_overwritten_or_announced(identity):
    g, tid, hold, api = identity
    g._reid["owner"].rebind_to = 99
    hold["event"] = threading.Event()
    hold["entered"].clear()
    in_flight = push(g, "B", 106)
    assert hold["entered"].wait(3)
    with g._lock:                                          # the operator picks someone else meanwhile
        g._operator_select(555, True)
        gen = g._lockid["gen"]
    hold["event"].set()
    hold["event"] = None
    assert not published(g, in_flight, timeout=0.4), "a frame decided for the old selection was published"
    with g._lock:
        assert g._lockid == {"id": 555, "pinned": True, "gen": gen}
        assert g._reid["owner"].tid == tid                # the discarded decision changed nothing authoritative
    snap = api()
    assert consistent(snap) and snap["stats"]["identity"]["rebound_from"] is None
    assert published(g, push(g, "B", 107))                 # the next frame belongs to the new selection
    snap = api()
    assert snap["stats"]["identity"]["selection_gen"] == gen and snap["stats"]["identity"]["target"] == 555 and consistent(snap)


def test_a_new_camera_run_discards_the_in_flight_decision_and_ends_the_identity(identity):
    g, tid, hold, api = identity
    g._reid["owner"].rebind_to = 99
    hold["event"] = threading.Event()
    hold["entered"].clear()
    in_flight = push(g, "B", 108)
    assert hold["entered"].wait(3)
    new_run = push(g, "C", 0)
    hold["event"].set()
    hold["event"] = None
    assert published(g, new_run)
    assert not published(g, in_flight, timeout=0.3)
    snap = api()
    with g._lock:
        assert g._lockid["id"] is None and g._lockid["pinned"] is False
        assert g._reid["owner"] is None or g._reid["owner"].tid is None
    identity_block = snap["stats"].get("identity")
    assert identity_block is None or (identity_block["target"] is None and identity_block["rebound_from"] is None)
    assert snap["lock"]["id"] is None


def test_vehicle_mount_publishes_a_shadow_command_for_the_published_frame_only(mono):
    g = mono
    for n in range(4):
        assert published(g, push(g, "V", 300 + n))
    with g._lock:
        tid = int(g._state["tracks_full"][0][0])
        g._operator_select(tid, True)
    assert published(g, push(g, "V", 310))
    with g._lock:
        assert g._state.get("onboard") is None            # mount is fixed
        g._mount["value"] = "vehicle"; g._onboard.reset()
    try:
        # The test's capture clock runs ahead of real time, and a frame "from the future" is held
        # as stale. Check that, then let real time catch up (capture time must not go backwards).
        seq = push(g, "V", 311)
        assert published(g, seq)
        with g._lock:
            assert g._state["onboard"]["valid"] is False and g._state["onboard"]["reason"] == "stale frame"
        time.sleep(max(0.0, CLOCK["t"] + 1 / 15 - time.monotonic()) + 0.01)
        seq = push(g, "V", 314)
        assert published(g, seq)
        with g._lock:
            o = dict(g._state["onboard"])
        assert o["frame_seq"] == seq and o["instance"] == g.INSTANCE and o["target_id"] == tid
        assert o["valid"] and o["range_source"] == "stereo" and o["range_m"] == 1.5      # FakeBox distance
        assert o["vertical_vel"] == 0.0 and abs(o["forward_vel"]) <= o["limits"]["max_back_vel"]
        with g._lock:
            g._onboard.forward = 0.3
            g._operator_select(None, False)
            # astra-0091: releasing the person withdraws the published command and the limiter's
            # memory at once, not at the next frame.
            assert g._state["onboard"] is None and g._onboard.forward == 0.0 and g._onboard.t is None
        seq = push(g, "V", 315, dt=0.001)
        assert published(g, seq)
        with g._lock:
            o = dict(g._state["onboard"])
        assert o["valid"] is False and o["reason"] == "no person selected" and o["forward_vel"] == 0.0
    finally:
        with g._lock:
            g._mount["value"] = "fixed"
    assert published(g, push(g, "V", 316))
    with g._lock:
        assert g._state.get("onboard") is None


def _held_step(g):
    """Make the next onboard computation finish and then wait, so the test can change the mount
    or the selection while that frame's command exists but is not yet published."""
    real, entered, release = g._onboard.step, threading.Event(), threading.Event()

    def step(obs, t):
        out = real(obs, t)
        entered.set()
        release.wait(5)
        g._onboard.forward, g._onboard.t = 0.3, t     # the step writes its memory AFTER the boundary
        return dict(out, forward_vel=0.3)
    g._onboard.step = step
    return real, entered, release


@pytest.mark.parametrize("change", ["mount", "selection"])
def test_a_command_computed_across_a_mount_or_selection_change_is_never_installed(mono, change):
    """astra-0092: the boundary arrives while a frame's command is in flight."""
    g = mono
    time.sleep(max(0.0, (CLOCK["t"] or 0) + 1 / 15 - time.monotonic()) + 0.01)
    for n in range(4):
        assert published(g, push(g, "W" + change, n, dt=0.001))
    with g._lock:
        tid = int(g._state["tracks_full"][0][0])
        g._operator_select(tid, True)
        g._set_mount("vehicle")
    assert published(g, push(g, "W" + change, 10, dt=0.001))
    with g._lock:
        assert g._state["onboard"]["valid"] is True
    real, entered, release = _held_step(g)
    try:
        seq = push(g, "W" + change, 11, dt=0.001)
        assert entered.wait(3)
        with g._lock:
            if change == "mount":
                g._set_mount("fixed")
            else:
                g._operator_select(None, False)
            assert g._state["onboard"] is None            # withdrawn at the boundary itself
        release.set()
        if change == "mount":
            assert published(g, seq)                      # the frame is published, without a command
        else:
            time.sleep(0.3)                               # the frame is dropped (old selection)
            with g._lock:
                assert g._state.get("frame_seq") != seq
        with g._lock:
            assert g._state["onboard"] is None, "the in-flight command must not be installed"
            assert g._onboard.forward == 0.0 and g._onboard.t is None, "nor may the limiter keep its speed"
    finally:
        g._onboard.step = real
        release.set()
        with g._lock:
            g._set_mount("fixed")
            g._operator_select(None, False)
    assert published(g, push(g, "W" + change, 12, dt=0.001))
    with g._lock:
        assert g._state.get("onboard") is None


def test_depth_http_views_identify_the_displayed_pair_and_its_age(mono, monkeypatch):
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    from oak_stereo_state import PairDepthStore
    import cv2

    store = PairDepthStore()
    rgb = np.full((16, 32, 3), 120, np.uint8)
    meta = dict(epoch="view", rgb=dict(seq=9, height=16, width=32,
                                       capture_host_ns=time.monotonic_ns()-100_000_000))
    store.put(types.SimpleNamespace(meta=meta, rgb=rgb.tobytes(), depth_mm=np.full((16, 32), 1234, np.uint16)))
    monkeypatch.setitem(mono._oak, "store", store)
    with ThreadingHTTPServer(("127.0.0.1", 0), mono.make_handler()) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for route in ("/stereo_depth.jpg", "/stereo_depth.jpg?paired=1", "/stereo_cloud.bin"):
                with urlopen(f"http://127.0.0.1:{server.server_port}{route}", timeout=2) as r:
                    assert r.headers["X-Stereo-Pair"] == "view:9"
                    assert int(r.headers["X-Depth-Age-Ms"]) >= 100
                    body = r.read()
                if route.endswith(".bin"):
                    assert body.startswith(b"GCL1")
                    assert int.from_bytes(body[8:10], "little") == 1234
                else:
                    frame = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
                    assert frame.shape[:2] == (8, 32 if "paired" in route else 16)
        finally:
            server.shutdown()
            thread.join()
