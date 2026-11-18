#!/usr/bin/env python3
"""garuda-mono (:8098) — single COLOR camera perception (YOLO26 + YOLO26-depth metric, HailoRT 5.3.0).

Self-contained: owns the CM3 (rpicam-vid MJPEG), runs YOLO26 detect + YOLO26-depth METRIC
depth on ONE shared vdevice, samples depth (metres) over the locked target's bbox; optional cal ->
metric live, and serves its OWN web GUI at http://<pi>:8098/ (video + target picker + distance +
calibration). No portal needed. Compute only while /stream has a viewer.
LICENCE: YOLO26 (Ultralytics) AGPL-3.0 — bench/eval, not-product-cleared.
"""
import glob, json, os, subprocess, threading, time, uuid
import numpy as np
import cv2
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import sys
sys.path.insert(0, "/home/garuda/garuda-sdk-deploy/hosts/pi5")
sys.path.insert(0, "/home/garuda/garuda-sdk-deploy/services/garuda-track")
from mono_timing import Progress
_progress = Progress()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hailo_platform import VDevice, FormatType
from hailo_yolo26 import HailoYolo26, COCO
from hailo_yolo26_depth import Yolo26Depth
from hailo_yolo_world import HailoYoloWorld, N_SLOTS, embed_prompts, set_prompt_checked
from hailo_yolo_pose import HailoYoloPose
from bytetrack import ByteTracker
from mono_wait import parse_after, event_stream, WAIT_LIMIT_S
from mono_state import mount_setting, onboard_obs, build_snapshot, capabilities, attach_kpts, select_by_id, select_track, positive_range, range_valid, prune_unmatched, note_matched
from mono_demo import asset as demo_asset
from mono_appearance import describe_people
from mono_faces import FaceWorker
from mono_gestures import classify_pose, GestureFSM, track_gestures

YOLO = os.environ.get("GARUDA_MONO_YOLO", "/home/garuda/garuda-sdk-deploy/models/yolo26m.hef")
WORLD_HEF = "/home/garuda/garuda-sdk-deploy/models/yolo_world_v2s_h10.hef"
POSE_HEF = os.environ.get("GARUDA_MONO_POSE", "/home/garuda/garuda-sdk-deploy/models/yolov8m_pose_h10.hef")
DA_HEF = "/home/garuda/garuda-sdk-deploy/models/depth_anything_v2_vits.hef"
Y26D_HEF = "/home/garuda/garuda-sdk-deploy/models/yolo26m-depth_hailo_model/yolo26m-depth.hef"
DA_IN, DA_OUT = "depth_anything_v2_vits/input_layer1", "depth_anything_v2_vits/conv79"
CAM_W, CAM_H, CAM_FPS = 1280, 720, 15
CAM_SRC = os.environ.get("GARUDA_MONO_CAM", "auto")  # auto = CSI, else USB UVC | csi = CM3 only | /dev/videoN = that device only
SKIP_MONO_DEPTH = os.environ.get("GARUDA_MONO_SKIP_DEPTH", "0") == "1"
DEPTH_EVERY = int(os.environ.get("GARUDA_MONO_DEPTH_EVERY", "3"))  # DepthAnything every Nth frame; YOLO26 runs every frame
CONF = float(os.environ.get("GARUDA_MONO_CONF", "0.15"))          # low floor -> ByteTrack's low-conf stage re-associates flickers
MAX_LOST = int(os.environ.get("GARUDA_MONO_MAXLOST", "25"))
# Wall-time bound on re-attaching an unmatched track (review item R3). The frame-count window
# above stretches when frames stall; this does not. Default = the same window at the nominal rate.
MAX_LOST_S = float(os.environ.get("GARUDA_MONO_MAXLOST_S", str(MAX_LOST / 15.0)))
# Optional person re-identification embeddings (EVAL-ONLY model; off unless a HEF path is given).
REID_HEF = os.environ.get("GARUDA_MONO_REID_HEF", "")
REID_BENCH_CROPS = int(os.environ.get("GARUDA_MONO_REID_BENCH_CROPS", "0"))   # bench only: repeat crops to this count to measure cost
_reid = {"model": None, "embeddings": {}, "ms": None, "crops": 0, "owner": None}
from mono_record import Recorder, frame_record, onboard_record
# Camera-on-vehicle shadow command: computed here, published in /api/state, optionally recorded.
# This process has no flight-controller link of any kind; nothing below transmits a command.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "services", "garuda-follow"))
from onboard_command import OnboardConfig, OnboardFollower, limits as onboard_limits
_mount_value, MOUNT_LOCKED = mount_setting(os.environ.get("GARUDA_MONO_MOUNT"))   # fixed | vehicle; unset = bench, switchable
_mount = {"value": _mount_value, "gen": 0}
_onboard = OnboardFollower(OnboardConfig())
from mono_transmit import Transmit
_transmit = Transmit()     # where the operator asked the command to go; this process itself sends nothing
def _set_mount(value):
    """Caller holds _lock. Withdraws the published command and the limiter's memory, and makes
    any computation still in flight for the old mount unpublishable (generation check)."""
    _mount.update(value=value, gen=_mount["gen"] + 1)
    _onboard.reset()
    _transmit.off("camera mount changed")
    _state.update(onboard=None)


_shadow = Recorder(os.path.expanduser("~/garuda-onboard-records"))   # numbers only, bounded, started on request
import copy
from mono_identity import IdentityOwner
# The identity owner keeps the pinned selection across track-ID changes (needs re-ID embeddings).
# It is only ever replaced, together with the frame its decision belongs to, at publication.
_recorder = Recorder(os.path.expanduser("~/garuda-reid-records"))    # numbers only, started on request, self-stopping
REACQ = int(os.environ.get("GARUDA_MONO_REACQ", "5"))            # coasting frames before yielding to a live same-class track       # coast a locked track this many frames through misses (~1s)
RANGE_STALE_MS = 1500
CAL_FILE = os.path.expanduser("~/garuda-sdk-deploy/hosts/pi5/mono_cal_oak.json" if CAM_SRC == "oak" else "~/garuda-sdk-deploy/hosts/pi5/mono_cal.json")  # calibration belongs to the camera
DEPTH_SRC = os.environ.get("GARUDA_MONO_DEPTH", "y26d")               # y26d = metric YOLO26-depth | da = DepthAnything relative (needs cal)
WORLD_CONF = float(os.environ.get("GARUDA_MONO_WORLD_CONF", "0.10"))  # yolo_world scores sit lower than YOLO26's
WORLD_MAX_FAILS = int(os.environ.get("GARUDA_MONO_WORLD_MAX_FAILS", "10"))  # consecutive world-infer errors -> auto-revert
POSE_MAX_FAILS = int(os.environ.get("GARUDA_MONO_POSE_MAX_FAILS", "10"))    # consecutive pose-infer errors -> auto-revert
OAK_PAIR = os.environ.get("GARUDA_MONO_OAK_PAIR", "0") == "1"       # 1 = fetch RGB+stereo depth via /pair (needs the stereo owner); 0 = RGB-only /frame
OAK_BOX = os.environ.get("GARUDA_MONO_OAK_BOX", "0") == "1"         # 1 = RGB via /frame, locked person's depth via the owner's /box-depth (host stereo)
_oak = {"store": None, "backend": None, "view_t": 0.0, "box": None}            # stereo pair store / backend (set only with OAK_PAIR); last depth-view request
_engine = {"name": DEPTH_SRC}                                     # live depth-engine selector (boot default DEPTH_SRC; POST /api/depth_engine switches it)
# live detector selector: primary = COCO-80 YOLO26 | world = prompt-driven yolo_world_v2s | pose = yolov8m_pose.
# `gen` bumps on every swap so loop() knows to reset the tracker (the label space changed).
_detector = {"mode": "primary", "prompt": [], "gen": 0}
_world = {"det": None}                                            # HailoYoloWorld, configured lazily on first world switch
_pose = {"det": None}                                             # HailoYoloPose, configured lazily on first pose switch
_gest = {"fsm": None, "handoff_fsm": None, "handoff_last_id": None,  # subject/bystander GestureFSMs (pose mode only)
         "out": {"name": None, "track_id": None, "held_ms": 0}}
_vd = {"h": None}                                                 # the shared VDevice (set in main)
_lockid = {"id": None, "pinned": False, "gen": 0}                 # gen counts operator selections; results for an older gen are not published


def _operator_select(tid, pinned):
    """Every selection change that is not the identity owner following the same person.
    Caller holds _lock."""
    _lockid.update(id=tid, pinned=pinned, gen=_lockid["gen"] + 1)
    _state["onboard"] = None        # the published vehicle command belonged to the old selection
    _onboard.reset()                 # and so does the limiter's memory
    _transmit.off("selection changed")   # sending never carries over to another selection
                           # explicit clicks never hand off silently
_ema = {"raw": None}                                              # smoothed depth (steady distance readout)
EMA_A = float(os.environ.get("GARUDA_MONO_EMA", "0.3"))

_lock = threading.Lock()
_frame_ready = threading.Event()     # set when an OAK frame arrives, so loop() need not poll for it
_result = threading.Condition()      # notified after each published result, for waiting /api/state reads
_det_lock = threading.Lock()      # serialises detector swap (create/configure) against loop()'s infer
_frame = {"img": None, "seq": 0, "t": 0.0, "source": None, "epoch": uuid.uuid4().hex}
_state = {"class": "person", "raw": None, "bbox": None, "conf": 0.0, "count": 0, "t": 0.0,
          "dets": [], "tracks_full": []}
_cal = []
_fit = None
_jpg = {"buf": None}
_streams = {"clean": 0, "annotated": 0}
_faces = FaceWorker(os.path.expanduser("~/garuda-sdk-deploy/models/faces"))
_track_gestures = {}
_stats = {"ids": set(), "fps": 0.0, "_n": 0, "_t0": time.monotonic(), "temp": None, "_tt": 0.0,
          "inferences": 0}                                         # completed loop() iterations since process start
INSTANCE = uuid.uuid4().hex                                        # identifies this process; counters restart with it
_transmit.instance = INSTANCE                                      # sender statements about a request must name this process


def _stats_tick(tracks_full):
    """Demo telemetry: processed-fps over a 2 s window + unique track ids since boot."""
    for t in tracks_full:
        _stats["ids"].add(int(t[0]))
    _stats["_n"] += 1
    now = time.monotonic()
    if now - _stats["_t0"] >= 2.0:
        _stats["fps"] = _stats["_n"] / (now - _stats["_t0"]); _stats["_n"] = 0; _stats["_t0"] = now


def _soc_temp():
    """SoC temperature, read at most every 2 s."""
    now = time.monotonic()
    if now - _stats["_tt"] > 2.0:
        try:
            _stats["temp"] = round(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000.0, 1)
        except Exception:
            _stats["temp"] = None
        _stats["_tt"] = now
    return _stats["temp"]
_viewers = {"n": 0}


def _refit():
    global _fit
    if len(_cal) < 2:
        _fit = None; return
    raw = np.array([c[0] for c in _cal], float); d = np.array([c[1] for c in _cal], float)
    def lsq(x):
        a, b = np.linalg.lstsq(np.vstack([x, np.ones(len(x))]).T, d, rcond=None)[0]
        return float(a), float(b), float(np.sum((a * x + b - d) ** 2))
    la, lb, ls = lsq(raw)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(raw != 0, 1.0 / raw, 0.0)
    ia, ib, isse = lsq(inv)
    _fit = ("inverse", ia, ib, isse) if isse < ls else ("linear", la, lb, ls)


def _save_cal():
    try:
        json.dump(_cal, open(CAL_FILE, "w"))
    except Exception:
        pass


def _load_cal_file():
    try:
        _cal[:] = [tuple(c) for c in json.load(open(CAL_FILE))]
        _refit()
    except Exception:
        pass


def _est(raw):
    if raw is None:
        return None
    if not _fit:
        return raw if (_engine["name"] != "da") else None   # metric raw IS metres; relative raw needs a cal fit
    mode, a, b, _ = _fit
    return a * (1.0 / raw if (mode == "inverse" and raw) else raw) + b


def capture_thread():
    """Keep the latest frame from the configured camera (drops backlog so the view stays live).

    GARUDA_MONO_CAM: auto (default) = CSI if a camera answers, else first working USB UVC,
    re-probing both whenever the active source dies | csi = CM3 only | /dev/videoN = that device only."""
    if CAM_SRC == "oak":
        frame_key = None
        if OAK_PAIR:                                     # stereo modules are imported ONLY on this path, so
            from oak_capture import OakPairBackend       # the default path runs without them being deployed
            from oak_stereo_state import PairDepthStore, frame_key
            store = PairDepthStore()
            def on_pair(pair):
                with _lock:
                    store.put(pair)
            def on_epoch_change(_epoch):                 # old run's depth is unusable; loop() resets itself from the frame epoch
                with _lock:
                    store.clear()
            backend = OakPairBackend(on_pair=on_pair, on_epoch_change=on_epoch_change,
                                     # depth bytes while a target is locked or someone is watching the depth view
                                     want_depth=lambda: (_lockid["id"] is not None
                                                         or time.monotonic() - _oak["view_t"] < 3.0))
            _oak.update(store=store, backend=backend)
        else:
            from oak_capture import OakRGBBackend
            backend = OakRGBBackend()
            if OAK_BOX:                                  # host stereo: number per frame from /box-depth, full maps only for the views
                from oak_stereo_state import PairDepthStore, frame_key
                from oak_box import BoxStereo
                from oak_pair import read_pair
                _oak.update(store=PairDepthStore(), box=BoxStereo())
                threading.Thread(target=_oak_view_loop, args=(read_pair,), daemon=True).start()
        backend.configure(CAM_W, CAM_H, CAM_FPS)
        def on_frame(meta, pixels):
            img = np.frombuffer(pixels, dtype=np.uint8).reshape(meta.height, meta.width, 3)
            with _lock:
                _frame.update(img=img, received_ns=time.monotonic_ns(), seq=_frame["seq"]+1, t=meta.t_capture_ns/1e9,
                              source="oak-rgb", epoch=meta.sensor, capture_ns=meta.t_capture_ns,
                              pair_key=frame_key(meta.sensor, meta.frame_id) if frame_key else None)
            _frame_ready.set()
        backend.start(on_frame)
        return
    while True:
        if CAM_SRC == "csi":
            _run_csi()
        elif CAM_SRC != "auto":
            _run_v4l2(CAM_SRC)
        elif not _run_csi():
            for dev in _usb_cams():
                if _run_v4l2(dev):
                    break
        time.sleep(1.0)


def _usb_cams():
    """V4L2 capture nodes that sit on USB (UVC webcams); skips the Pi's ISP/codec nodes."""
    return ["/dev/" + os.path.basename(p) for p in sorted(glob.glob("/sys/class/video4linux/video*"))
            if "usb" in os.path.realpath(os.path.join(p, "device"))]


def _run_v4l2(dev):
    """One capture session on a V4L2 device (MJPG, latest-frame-wins). True if it ever delivered."""
    got = False
    session_epoch = uuid.uuid4().hex
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
    cap.set(cv2.CAP_PROP_FPS, CAM_FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    try:
        while True:
            ok, img = cap.read()
            if not ok or img is None:
                break
            if not got:
                got = True; print(f"capture: {dev} (V4L2)", flush=True)
            with _lock:
                _frame.update(img=img, seq=_frame["seq"] + 1, t=time.monotonic(), source=dev, epoch=session_epoch)
    except Exception:
        pass
    try:
        cap.release()
    except Exception:
        pass
    return got


def _run_csi():
    """One rpicam-vid MJPEG session (CM3 on CSI). True if it ever delivered a frame; when no
    CSI camera is present rpicam-vid exits by itself, so the auto probe falls through quickly."""
    got = False
    session_epoch = uuid.uuid4().hex
    p = subprocess.Popen(
        ["rpicam-vid", "-t", "0", "-n", "--camera", "0", "--codec", "mjpeg",
         "--width", str(CAM_W), "--height", str(CAM_H), "--framerate", str(CAM_FPS),
         "--inline", "-o", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    buf = b""
    try:
        while True:
            c = p.stdout.read(8192)
            if not c:
                break
            buf += c
            while True:
                a = buf.find(b"\xff\xd8"); e = buf.find(b"\xff\xd9")
                if a != -1 and e != -1 and e > a:
                    img = cv2.imdecode(np.frombuffer(buf[a:e + 2], np.uint8), cv2.IMREAD_COLOR)
                    buf = buf[e + 2:]
                    if img is not None:
                        if not got:
                            got = True; print("capture: csi (rpicam-vid)", flush=True)
                        with _lock:
                            _frame.update(img=img, seq=_frame["seq"] + 1, t=time.monotonic(), source="csi", epoch=session_epoch)
                else:
                    break
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass
    return got


class DepthAnything:
    def __init__(self, vd):
        self.im = vd.create_infer_model(DA_HEF); self.im.set_batch_size(1); self.cm = self.im.configure()
    def infer(self, bgr):
        rgb = cv2.cvtColor(cv2.resize(bgr, (224, 224)), cv2.COLOR_BGR2RGB)
        b = self.cm.create_bindings()
        b.input(DA_IN).set_buffer(np.ascontiguousarray(rgb, np.uint8))
        out = np.zeros((224, 224, 1), np.uint16); b.output(DA_OUT).set_buffer(out)
        self.cm.run([b], 5000)
        return out[:, :, 0].astype(np.float32)


def _sample(depth, bbox, W, H):
    x, y, w, h = bbox
    mh, mw = depth.shape[:2]
    sx, sy = float(mw) / W, float(mh) / H
    x0, y0 = max(0, int(x * sx)), max(0, int(y * sy))
    x1, y1 = min(mw, int((x + w) * sx)), min(mh, int((y + h) * sy))
    patch = depth[y0:y1, x0:x1]
    if not patch.size:
        return None
    pos = patch[patch > 0]
    return float(np.median(pos)) if pos.size else float(np.median(patch))


def _new_tracker():
    return ByteTracker(high_thr=0.4, low_thr=0.12, iou_thr=0.3, max_lost=MAX_LOST, min_hits=2)


def _activate_world(vd, prompt, emb, probe_img):
    """Configure (once) + prompt + PROVE the open-vocab detector. Raises on any failure.

    The prompt swap and the proving inference are staged together: if either fails, the live
    detector's vocabulary is restored exactly, so `_world["det"]` can never end up running a
    vocabulary that `_detector["prompt"]` (and therefore the snapshot) does not report.
    """
    if _world["det"] is None:
        _world["det"] = HailoYoloWorld(WORLD_HEF, vdevice=vd, conf_thresh=WORLD_CONF)
    # emb -> slots 0..n-1, unused slots zero-filled (measured best); probe proves the whole path
    return set_prompt_checked(_world["det"], prompt, emb, None, probe_img)


def _oak_view_loop(read_pair):
    """Box mode: fetch full depth maps from the owner only while a depth view is open.
    The owner computes them on demand, so a closed view costs nothing. read_pair is handed in
    by the box-mode start-up, so this module needs no stereo import outside a stereo flag."""
    from urllib.request import urlopen
    cursor = None
    while True:
        if time.monotonic() - _oak["view_t"] >= 3.0:
            time.sleep(0.2); continue
        try:
            q = "/pair-preview?depth=1" + (f"&after={cursor[1]}&epoch={cursor[0]}" if cursor else "")
            with urlopen("http://127.0.0.1:8096" + q, timeout=3) as r:
                if r.status == 204:
                    continue
                pair = read_pair(r, int(r.headers["Content-Length"]), (CAM_W, CAM_H), allow_unqualified_preview=True)
            cursor = pair.key
            if pair.depth_mm is not None:
                with _lock:
                    _oak["store"].put(pair)
        except Exception:
            time.sleep(0.3)


def loop(det, engines):
    tracker = _new_tracker()
    last_seq = -1; n = 0; depth = None; cur_engine = _engine["name"]; cur_gen = _detector["gen"]
    fails = 0; was_world = False; was_pose = False
    matched_t = {}                                       # track id -> capture time of its last matched frame
    gate = None
    if OAK_PAIR or OAK_BOX:                              # both stereo modes carry camera epochs: same restart rules
        from oak_stereo_state import EpochGate
        gate = EpochGate()
    while True:
        if _viewers["n"] == 0:
            time.sleep(0.2); continue
        with _lock:
            img = _frame["img"]; seq = _frame["seq"]
            capture_t = _frame["t"]; capture_source = _frame["source"]; capture_epoch = _frame["epoch"]
            received_ns = _frame.get("received_ns")
            pair_key = _frame.get("pair_key"); capture_ns = _frame.get("capture_ns")
        if img is None or seq == last_seq:
            _frame_ready.wait(0.02); _frame_ready.clear(); continue
        last_seq = seq
        inference_start_ns = time.monotonic_ns()
        H, W = img.shape[:2]
        with _lock:
            want_cls = _state["class"]; lid = _lockid["id"]; pinned = _lockid["pinned"]; sel_gen = _lockid["gen"]
        staged_owner = None; decision = None; operator_lid = lid
        if _engine["name"] != cur_engine:                # live engine switch -> drop stale depth/EMA
            depth = None; _ema["raw"] = None; cur_engine = _engine["name"]
        # OAK owner restarted -> everything derived from the old run is discarded. Decided from the
        # epoch of the frame being processed (after the skip check above), so it cannot be lost.
        if gate is not None and _oak["store"] is not None and gate.entering(capture_epoch):
            tracker = _new_tracker(); _track_gestures.clear(); _ema["raw"] = None; depth = None
            _gest["fsm"] = GestureFSM(); _gest["handoff_fsm"] = GestureFSM(); _gest["handoff_last_id"] = None
            with _lock:
                _operator_select(None, False); sel_gen = _lockid["gen"]   # this frame is processed under the reset selection
                _gest["out"] = {"name": None, "track_id": None, "held_ms": 0}
                _state.update(raw=None, bbox=None, conf=0.0, count=0, tracks_full=[], dets=[],
                              stereo=None, lock_id=None, lock_coasting=False, identity=None)
                _reid["owner"] = None                    # the old run's identity and gallery end with it
                if OAK_BOX:                              # the old run's full maps must not reach the depth views
                    _oak["store"].clear()
            lid = None; pinned = False
        if _detector["gen"] != cur_gen:                  # detector swap -> label space changed, restart tracking
            tracker = _new_tracker(); cur_gen = _detector["gen"]
            _track_gestures.clear()
            with _lock:
                _operator_select(None, False); sel_gen = _lockid["gen"]   # this frame is processed under the reset selection
            lid = None; pinned = False
        try:
            with _det_lock:                             # never infer while a swap is (re)configuring
                mode_now = _detector["mode"]
                was_world = mode_now == "world"
                was_pose = mode_now == "pose"
                active = _pose["det"] if was_pose else (_world["det"] if was_world else det)
                dets = active.infer(img)                # detector EVERY frame -> feeds the tracker
            fails = 0
            if not SKIP_MONO_DEPTH and (depth is None or n % DEPTH_EVERY == 0):
                depth = engines[cur_engine].infer(img)  # active depth engine periodically (for distance)
        except Exception as e:
            print("mono infer error:", e)
            fails += 1
            if was_world and fails >= WORLD_MAX_FAILS:  # a wedged world path must not strand the view
                print(f"world detector failed {fails}x -> reverting to primary")
                with _det_lock:
                    _detector.update(mode="primary", prompt=[], gen=_detector["gen"] + 1)
                fails = 0
            elif was_pose and fails >= POSE_MAX_FAILS:  # a wedged pose path must not strand the view
                print(f"pose detector failed {fails}x -> reverting to primary")
                with _det_lock:
                    _detector.update(mode="primary", prompt=[], gen=_detector["gen"] + 1)
                fails = 0
            time.sleep(0.2); continue
        n += 1
        # A track not matched for longer than MAX_LOST_S of capture time cannot be re-attached under
        # its old ID, however few frames went by in between.
        tracker.tracks = prune_unmatched(tracker.tracks, matched_t, capture_t, MAX_LOST_S)
        tracker.update([((d.x, d.y, d.w, d.h), d.label, float(d.conf)) for d in dets])
        note_matched(tracker.tracks, matched_t, capture_t)
        alive = [t for t in tracker.tracks if t.confirmed and t.time_since_update <= MAX_LOST]
        if _reid["model"] is not None:                  # embeddings for the people seen in THIS frame; no decision is made here
            seen_people = sorted((t for t in alive if t.cls == "person" and t.time_since_update == 0 and t.conf >= 0.5),
                                 key=lambda t: (t.track_id != lid, -t.conf))[:4]
            boxes = [tuple(t.bbox) for t in seen_people]
            if REID_BENCH_CROPS and boxes:
                boxes = (boxes * REID_BENCH_CROPS)[:min(4, REID_BENCH_CROPS)]
            try:
                with _det_lock:
                    vectors = _reid["model"].embed(img, boxes)
                timing = _reid["model"].last_timing
                _reid.update(embeddings={t.track_id: v for t, v in zip(seen_people, vectors) if v is not None},
                             ms=timing["total_ns"] / 1e6, crops=timing["crops"], frame_seq=seq)
            except Exception as e:                      # a re-ID fault must never stop detection
                print("re-id error:", e)
                _reid.update(embeddings={}, ms=None, crops=0)
            # One identity decision for everyone downstream: if the pinned person's track ID
            # changed, move the camera's own selection to the new ID (or hold when unsure).
            # Worked out on a COPY: nothing authoritative changes until this frame is published
            # (after the camera-run and selection checks), so a result that is thrown away can
            # neither move the selection nor announce a re-bind (astra-0062 finding 2).
            if pinned and lid is not None:
                staged_owner = copy.deepcopy(_reid["owner"]) if _reid["owner"] is not None else IdentityOwner()
                if staged_owner.tid is None or getattr(staged_owner, "selection_gen", None) != sel_gen:
                    staged_owner.select(lid, capture_t)  # an operator selection starts a fresh identity
                    staged_owner.selection_gen = sel_gen
                decision = staged_owner.update(capture_t, [dict(id=t.track_id, emb=_reid["embeddings"].get(t.track_id), pos=None)
                                                           for t in alive if t.cls == "person" and t.time_since_update == 0])
                lid = decision["target"]                # this frame's lock and stereo are for the decided track
            else:
                staged_owner = IdentityOwner()
                decision = staged_owner.summary(capture_t)
        appearances = describe_people(img, alive)
        locked = select_track(alive, lid, want_cls, pinned, REACQ)
        with _lock:
            if _lockid["id"] == lid and not pinned:
                _lockid["id"] = locked.track_id if locked is not None else None
        if locked is not None:
            ebbox = (locked.bbox[0], locked.bbox[1], locked.bbox[2], locked.bbox[3])
            econf = float(locked.conf); coasting = locked.time_since_update > 0
        else:
            ebbox = None; econf = 0.0; coasting = False
        raw = _sample(depth, ebbox, W, H) if (ebbox is not None and depth is not None) else None
        if raw is not None:                              # EMA-smooth the depth -> steady distance
            _ema["raw"] = raw if _ema["raw"] is None else (1 - EMA_A) * _ema["raw"] + EMA_A * raw
            raw = _ema["raw"]
        else:
            _ema["raw"] = None
        seen = sorted({(d.label, round(float(d.conf), 2)) for d in dets}, key=lambda x: -x[1])[:10]
        tracks_full = [(t.track_id, t.cls, t.bbox[0], t.bbox[1], t.bbox[2], t.bbox[3],
                        float(t.conf), t.time_since_update > 0) for t in alive]
        if _detector["mode"] == "pose":
            tracks_full = attach_kpts(tracks_full, dets)
            kpt_tracks = [t for t in tracks_full if len(t) > 8]
            # Subject FSM: the locked track (by the pre-frame lock id) if it still carries kpts,
            # else the largest-area kpts-bearing track (lets FOLLOW ME work while unlocked).
            subject = next((t for t in kpt_tracks if t[0] == lid), None) if lid is not None else None
            if subject is None and kpt_tracks:
                subject = max(kpt_tracks, key=lambda t: t[4] * t[5])
            if subject is not None:
                subj_name = classify_pose(subject[8], subject[4], subject[5])
                subj_res = _gest["fsm"].update(subj_name)
                subj_id = subject[0]
            else:
                subj_res = _gest["fsm"].update(None)
                subj_id = None
            # Handoff FSM: ticked EVERY pose-mode frame (fix round 1 — previously the .update()
            # call was skipped whenever no bystander was visible, which broke GestureFSM's
            # gap/hold bookkeeping: an absence of ANY length, even minutes, would resume "active"
            # instantly on reappearance instead of requiring a fresh hold_ms, because the FSM's
            # internal clocks never advanced while un-ticked). Fed a real "follow_me" candidate
            # only while a lock exists AND a kpts-bearing bystander (not the locked track) is
            # gesturing it; None in every other case (no lock, no bystander, or a non-follow_me
            # bystander gesture) so the FSM's own symmetric hysteresis governs decay/reactivation
            # instead of freezing mid-hold. `handoff_last_id` remembers which track is behind the
            # current candidate/active hold, since a bystander can transiently drop out of frame
            # during the release_ms window while the FSM (correctly) stays active.
            byst_input = None
            if locked is not None:
                bystanders = [t for t in kpt_tracks if t[0] != locked.track_id]
                if bystanders:
                    byst = max(bystanders, key=lambda t: t[4] * t[5])
                    byst_name = classify_pose(byst[8], byst[4], byst[5])
                    if byst_name == "follow_me":
                        byst_input = "follow_me"
                        _gest["handoff_last_id"] = byst[0]
            byst_res = _gest["handoff_fsm"].update(byst_input)
            handoff_out = ({"name": byst_res["name"], "track_id": _gest["handoff_last_id"],
                           "held_ms": byst_res["held_ms"]} if byst_res["name"] is not None
                          else {"name": None, "track_id": None, "held_ms": 0})
            if handoff_out["name"] is not None:                 # handoff wins over the subject
                gest_out = handoff_out
            else:
                gest_out = {"name": subj_res["name"],
                           "track_id": subj_id if subj_res["name"] else None,
                           "held_ms": subj_res["held_ms"]}
        else:
            gest_out = {"name": None, "track_id": None, "held_ms": 0}
            _gest["fsm"] = GestureFSM(); _gest["handoff_fsm"] = GestureFSM()
            _gest["handoff_last_id"] = None
        stereo = None
        if OAK_PAIR and _oak["store"] is not None:      # stereo depth of the locked target, for THIS frame only
            from oak_stereo_state import measure_entry
            lk = next((t[8] for t in tracks_full if locked is not None and t[0] == locked.track_id and len(t) > 8), None)
            with _lock:                                 # lookup only; the depth array itself is never mutated
                entry = _oak["store"].get(pair_key) if pair_key is not None else None
            try:
                stereo = measure_entry(entry, pair_key, ebbox, lk, coasting,
                                       target_class=locked.cls if locked is not None else None)
            except Exception as e:                      # a stereo fault must never stop detection
                print("stereo measurement error:", e)
                stereo = {"status": "unavailable", "reason": "estimator_error", "z_m": None}
            stereo["capture"] = dict(_oak["backend"].counters)
        elif OAK_BOX and _oak["box"] is not None:       # host stereo, matched only in the locked person's torso region of THIS frame
            lk = next((t[8] for t in tracks_full if locked is not None and t[0] == locked.track_id and len(t) > 8), None)
            try:
                stereo = _oak["box"].measure(pair_key, capture_ns, ebbox, lk, coasting,
                                             target_class=locked.cls if locked is not None else None)
            except Exception as e:                      # a stereo fault must never stop detection
                print("box stereo error:", e)
                stereo = {"status": "unavailable", "reason": "estimator_error", "z_m": None}
            stereo["capture"] = dict(_oak["box"].counters)
        onboard = None; mount_gen = _mount["gen"]
        if _mount["value"] == "vehicle":                # shadow command for THIS frame; never transmitted
            try:
                caps = capabilities(capture_source, OAK_PAIR or OAK_BOX, not SKIP_MONO_DEPTH, _engine["name"], "vehicle")
                now_s = time.monotonic()
                onboard = _onboard.step(onboard_obs(tracks_full, locked.track_id if locked else None, coasting, W, H,
                                                    (now_s - capture_t) * 1000.0, decision, caps, stereo, pinned), now_s)
            except Exception as e:                      # a fault here must never stop detection
                print("onboard command error:", e)
                _onboard.reset()
                onboard = {"valid": False, "reason": "command error", "yaw_rate": 0.0, "forward_vel": 0.0, "vertical_vel": 0.0,
                           "target_id": None, "range_m": None, "range_source": "none", "forward_reason": "hold"}
            onboard.update(frame_seq=seq, frame_epoch=f"{capture_epoch}:{cur_gen}", instance=INSTANCE, limits=onboard_limits(_onboard.cfg))
        with _lock:
            if gate is not None and not gate.may_publish(capture_epoch, _frame["epoch"]):
                continue                                # a newer run's frame exists: do not publish old-run results
            if _lockid["gen"] != sel_gen:
                _onboard.reset()                        # this frame's step ran on the old selection: forget it
                continue                                # the operator changed the selection during this frame: its lock, stereo and identity are for the old one
            if staged_owner is not None:                # identity, selection and frame change together
                _reid["owner"] = staged_owner
                if pinned and operator_lid is not None and decision["target"] != _lockid["id"]:
                    _lockid["id"] = decision["target"]  # the owner following the same person: not a new operator selection
            _state.update(identity=dict(decision, frame_seq=seq, frame_epoch=f"{capture_epoch}:{cur_gen}",
                                        selection_gen=sel_gen, instance=INSTANCE) if decision is not None else None)
            _gest["out"] = gest_out
            if _mount["gen"] != mount_gen or _mount["value"] != "vehicle":
                if onboard is not None:
                    _onboard.reset()                    # computed across a mount change: never installed
                onboard = None
            _state.update(stereo=stereo, onboard=onboard)
            _state.update(raw=raw, bbox=ebbox, conf=econf, count=len(alive), t=capture_t,
                          frame_seq=seq, frame_source=capture_source, pair_key=pair_key if (OAK_PAIR or OAK_BOX) else None, lock_id=locked.track_id if locked else lid,
                          frame_epoch=f"{capture_epoch}:{cur_gen}", frame_w=W, frame_h=H,
                          dets=seen, tracks_full=tracks_full, appearances=appearances,
                          track_gestures=track_gestures(tracks_full, _track_gestures), lock_coasting=coasting)
        with _result:
            _result.notify_all()
        if _recorder.file is not None:                  # identity recording in progress (no pixels are stored)
            try:
                _recorder.write(frame_record(seq, capture_t, locked.track_id if locked else lid, pinned, tracks_full, stereo,
                                             _reid["embeddings"] if _reid.get("frame_seq") == seq else {}, appearances))
            except Exception as e:
                print("identity recorder error:", e); _recorder.close()
        if _shadow.file is not None:                    # carry-around recording in progress (no pixels are stored)
            try:
                _shadow.write(onboard_record(seq, capture_t, onboard, ebbox if locked is not None else None, stereo, _soc_temp()))
            except Exception as e:
                print("onboard recorder error:", e); _shadow.close()
        _faces.submit(img, tracks_full, seq, f"{capture_epoch}:{cur_gen}", capture_t)
        _stats_tick(tracks_full)
        _progress.record(seq,capture_epoch,int(capture_t*1e9),received_ns,inference_start_ns)
        _stats["inferences"] += 1                       # one per fully processed and published frame
        # The two views are independent: a fault drawing one must never freeze the other.
        try:
            if _streams["clean"]:
                ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    _jpg["clean"] = jpg.tobytes()
        except Exception as e:
            print("mono clean view error:", e)
        try:
            if _streams["annotated"]:
                _jpg["buf"] = _draw(img, depth, dets, ebbox, coasting, want_cls, raw)
        except Exception as e:
            print("mono draw error:", e)


def _draw(img, depth, dets, ebbox, coasting, cls, raw):
    view = img.copy(); h, w = view.shape[:2]
    if depth is not None:                               # no monocular depth in skip-depth mode: no thumbnail
        dn = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        thumb = cv2.applyColorMap(cv2.resize(dn, (150, 150)), cv2.COLORMAP_INFERNO)
        view[8:158, w - 158:w - 8] = thumb
        cv2.rectangle(view, (w - 158, 8), (w - 8, 158), (230, 230, 230), 1)
    for d in dets:                                   # all detections, faint
        cv2.rectangle(view, (int(d.x), int(d.y)), (int(d.x + d.w), int(d.y + d.h)), (150, 150, 150), 1)
        cv2.putText(view, f"{d.label} {d.conf:.2f}", (int(d.x), int(d.y) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
    if ebbox is not None:                            # the locked target, bold (dim while coasting)
        x, y, ww, hh = ebbox
        col = (0, 170, 90) if coasting else (0, 235, 0)
        cv2.rectangle(view, (int(x), int(y)), (int(x + ww), int(y + hh)), col, 2)
    est = _est(raw); tag = " (coast)" if coasting else ""
    if ebbox is None:
        banner = f"{cls}: searching..."
    elif raw is None:
        banner = f"{cls}: locked{tag}  (depth pending)"
    elif est is None:
        banner = f"{cls}: raw {raw:.0f}{tag}  (add 2+ cal points)"
    elif not positive_range(est):
        banner = f"{cls}: locked{tag}  (range invalid - recalibrate)"
    elif _fit:
        banner = f"{cls}: {est:.2f} m{tag}  (cal, raw {raw:.2f})"
    else:
        banner = f"{cls}: {est:.2f} m{tag}"
    cv2.putText(view, banner, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    ok, jpg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return jpg.tobytes() if ok else None


PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Garuda Mono — YOLO26 + metric depth</title>
<meta name=viewport content='width=device-width,initial-scale=1'>
<style>body{margin:0;background:#0f1419;color:#dfe7ef;font-family:-apple-system,Segoe UI,sans-serif}
header{padding:.6rem 1rem;border-bottom:1px solid #2a3540}h1{font-size:1rem;margin:0;color:#7fd1b9}
main{padding:1rem;max-width:1000px;margin:0 auto}.bar{display:flex;gap:1rem;align-items:center;flex-wrap:wrap;margin:.6rem 0}
select,input,button{background:#161c22;color:#dfe7ef;border:1px solid #2a3540;border-radius:5px;padding:.35rem .6rem;font-size:.85rem}
button{cursor:pointer}button:hover{border-color:#7fd1b9}.big{font-size:1.4rem;color:#7fd1b9;font-variant-numeric:tabular-nums}
.badge{font-size:.7rem;padding:2px 8px;border-radius:10px;background:#143028;color:#5ed5a0}
img{width:100%;border:1px solid #2a3540;border-radius:8px;display:block;background:#000}.muted{color:#6b7a88;font-size:.8rem}</style></head>
<body><header><h1>Garuda Mono — single CM3 · YOLO26 + YOLO26-depth metric (HailoRT 5.3.0)</h1></header><main>
<p class=muted><b>Click any detected object in the video to lock it</b> (or pick from the dropdown). YOLO26 detects, YOLO26-depth estimates <b>metric range in metres</b> directly — no calibration needed. Optional: add tape-measure cal points to refine the scale for this camera.</p>
<div class=bar>
<label>Target <select id=cls></select></label>
<span>raw <b id=raw class=big>—</b></span>
<span>est <b id=est class=big>—</b> <span id=fit class=badge></span></span>
</div>
<div class=bar>
<label>True distance (m) <input id=truth type=number step=.05 min=0 style=width:5rem></label>
<button onclick=cal()>Add cal point</button><button onclick=reset()>Reset cal</button>
<span id=callist class=muted>no calibration yet</span>
</div>
<canvas id=chart height=90 style='width:100%;background:#0b0f13;border:1px solid #2a3540;border-radius:8px;margin:.3rem 0'></canvas>
<img id=v src='/stream'>
<script>
const CLASSES=__CLASSES__;
const sel=document.getElementById('cls');CLASSES.forEach(c=>{let o=document.createElement('option');o.textContent=c;sel.appendChild(o)});
sel.value='person';sel.onchange=()=>fetch('/api/mono_target?class='+encodeURIComponent(sel.value),{method:'POST'});
const v=document.getElementById('v');v.onerror=()=>setTimeout(()=>{v.src='/stream?'+Date.now()},2000);
v.style.cursor='crosshair';
v.onclick=(e)=>{if(!v.naturalWidth)return;let r=v.getBoundingClientRect();
 let nx=(e.clientX-r.left)/r.width*v.naturalWidth,ny=(e.clientY-r.top)/r.height*v.naturalHeight;
 fetch('/api/mono_click?x='+Math.round(nx)+'&y='+Math.round(ny),{method:'POST'}).then(r=>r.json()).then(j=>{
  if(j.class){if(![...sel.options].some(o=>o.value===j.class))sel.add(new Option(j.class,j.class));sel.value=j.class}})};
function cal(){let d=parseFloat(document.getElementById('truth').value);if(!(d>0)){alert('enter a distance');return}
 fetch('/api/mono_calibrate?dist='+d,{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)alert('no target in view')})}
function reset(){fetch('/api/mono_calibrate_reset',{method:'POST'})}
setInterval(async()=>{let d;try{d=await(await fetch('/api/mono_range')).json()}catch(e){return}
 document.getElementById('raw').textContent=d.valid&&d.raw!=null?d.raw.toFixed(2)+' m':'—';
 document.getElementById('est').textContent=d.est_m!=null?d.est_m.toFixed(2)+' m':'—';
 let f=document.getElementById('fit');f.textContent=d.fit||(d.n_cal>=1?'need 2+':'');f.style.display=d.fit?'':'none';
 document.getElementById('callist').textContent=d.n_cal?d.n_cal+' cal: '+d.cal.map(c=>c[1]+'m@'+Math.round(c[0])).join(', '):'no calibration yet';
 hist.push({t:Date.now(),v:(d.valid&&d.est_m!=null)?d.est_m:null});draw();
},300);
const hist=[];const ch=document.getElementById('chart');
function draw(){const g=ch.getContext('2d');const W=ch.width=ch.clientWidth,H=ch.height;
 g.clearRect(0,0,W,H);const now=Date.now(),span=60000;
 while(hist.length&&now-hist[0].t>span)hist.shift();
 const vals=hist.filter(p=>p.v!=null).map(p=>p.v);
 if(!vals.length){g.fillStyle='#6b7a88';g.font='12px sans-serif';g.fillText('distance history (60 s) — waiting for lock...',8,16);return}
 let lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);const pad=Math.max(0.05,(hi-lo)*0.15);lo-=pad;hi+=pad;
 g.strokeStyle='#22303c';g.beginPath();for(let i=1;i<4;i++){const y=H*i/4;g.moveTo(0,y);g.lineTo(W,y)}g.stroke();
 g.strokeStyle='#7fd1b9';g.lineWidth=2;g.beginPath();let started=false;
 for(const p of hist){if(p.v==null){started=false;continue}
  const x=W-(now-p.t)/span*W,y=H-(p.v-lo)/(hi-lo)*H;
  if(!started){g.moveTo(x,y);started=true}else g.lineTo(x,y)}
 g.stroke();
 g.fillStyle='#6b7a88';g.font='11px sans-serif';g.fillText(hi.toFixed(2)+' m',6,12);g.fillText(lo.toFixed(2)+' m',6,H-4);
 const last=vals[vals.length-1];g.fillStyle='#7fd1b9';g.font='bold 13px sans-serif';g.fillText(last.toFixed(2)+' m',W-70,14);}
</script></main></body></html>"""

# Trade-show assets are served from mono_demo.py and demo/.


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def _send(self, code, ctype, body):
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
            self.wfile.write(body)
        def _snapshot(self):
            with _lock:
                s = dict(_state)
            s["faces"] = _faces.snapshot(s.get("frame_epoch"))
            est = _est(s["raw"])
            age = int((time.monotonic() - s["t"]) * 1000) if s["t"] else None
            valid = range_valid(s["raw"], est, _engine["name"], age, s.get("lock_coasting", False))
            return build_snapshot(s, {
                "lock_id": s.get("lock_id"), "lock_coasting": s.get("lock_coasting", False),
                "est_m": est if valid else None, "valid": valid, "age_ms": age,
                "depth_engine": _engine["name"],
                "detector_mode": _detector["mode"], "prompt": list(_detector["prompt"]),
                "cal_n": len(_cal), "fit": (_fit[0] if _fit else None),
                "frame_w": CAM_W, "frame_h": CAM_H,
                "gesture": dict(_gest["out"]), "face_service": _faces.status(),
                "onboard_max_age_ms": _onboard.cfg.max_age_ms,
                "transmit": _transmit.snapshot(time.monotonic()),
                "stats": {"timing": _progress.snapshot(), "fps": round(_stats["fps"], 1), "unique_total": len(_stats["ids"]),
                          "temp_c": _soc_temp(),
                          "inferences": _stats["inferences"], "instance": INSTANCE,
                          "capabilities": dict(capabilities(s.get("frame_source"), OAK_PAIR or OAK_BOX, not SKIP_MONO_DEPTH, _engine["name"], _mount["value"]),
                                               mount_locked=MOUNT_LOCKED, onboard_recording=_shadow.active()),
                          **({"reid": {"crops": _reid["crops"], "ms": _reid["ms"]}, "identity": s.get("identity")} if _reid["model"] is not None else {})}})
        def do_GET(self):
            p = urlparse(self.path).path
            if p in ("/", "/index.html"):
                self._send(200, "text/html", PAGE.replace("__CLASSES__", json.dumps(COCO)).encode())
            elif p == "/demo" or p.startswith("/demo/"):
                result = demo_asset(p)
                if result is None:
                    self._send(404, "text/plain", b"not found")
                else:
                    self._send(200, result[0], result[1])
            elif p == "/stereo_depth.jpg":             # colourised stereo depth, for looking at only
                latest = None
                if (OAK_PAIR or OAK_BOX) and _oak["store"] is not None:
                    from oak_depth_view import colourise, draw_box
                    _oak["view_t"] = time.monotonic()    # keeps depth flowing while the view is open
                    from oak_stereo_state import view_selection
                    paired = parse_qs(urlparse(self.path).query).get("paired") == ["1"]
                    with _lock:
                        block = _state.get("stereo") or {}
                        latest = view_selection(_oak["store"], block, time.monotonic_ns())
                        if latest is not None:
                            rgb = _oak["store"].rgb(latest[0]) if paired else None
                            capture_ns = _oak["store"].get(latest[0])[1]["rgb"]["capture_host_ns"]
                    if latest is None:
                        self._send(503, "text/plain", b"no fresh depth frame"); return
                    view = colourise(latest[1], max_mm=6000, step=2)
                    if latest[2] is not None:
                        draw_box(view, latest[2], step=2)
                    if paired:
                        if rgb is None or rgb.shape[:2] != latest[1].shape:
                            self._send(503, "text/plain", b"matching RGB unavailable"); return
                        left = cv2.resize(rgb, (view.shape[1], view.shape[0]), interpolation=cv2.INTER_AREA)
                        if latest[2] is not None:
                            draw_box(left, latest[2], step=2)
                        view = np.concatenate((left, view), axis=1)
                    ok, jpg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if not ok:
                        self._send(500, "text/plain", b"encode failed"); return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpg)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Stereo-Pair", f"{latest[0][0]}:{latest[0][1]}")
                    self.send_header("X-Depth-Age-Ms", str((time.monotonic_ns()-capture_ns+999_999)//1_000_000))
                    if paired and latest[2] is not None and block.get("status") == "ok":
                        self.send_header("X-Depth-Z-M", str(block["z_m"]))
                    self.send_header("X-Stereo-Outline", "matched" if latest[2] is not None else "none")
                    self.end_headers()
                    self.wfile.write(jpg.tobytes())
                else:
                    self._send(404, "text/plain", b"stereo is not enabled")
            elif p == "/stereo_cloud.bin":             # one pair's depth + colour on a coarse grid, for the 3-D view
                sel = None
                if (OAK_PAIR or OAK_BOX) and _oak["store"] is not None:
                    from oak_depth_view import cloud_payload, observed_cloud_payload
                    from oak_stereo_state import view_selection
                    _oak["view_t"] = time.monotonic()
                    query = parse_qs(urlparse(self.path).query)
                    step = {"2": 2, "4": 4}.get((query.get("step") or [""])[0], 8)
                    with _lock:
                        sel = view_selection(_oak["store"], _state.get("stereo"), time.monotonic_ns())
                        rgb = _oak["store"].rgb(sel[0]) if sel is not None else None
                        if sel is not None:
                            meta = _oak["store"].get(sel[0])[1]
                            geometry = meta.get("geometry")
                            capture_ns = meta["rgb"]["capture_host_ns"]
                    if sel is None or rgb is None:
                        self._send(503, "text/plain", b"no fresh depth frame"); return
                    try:
                        if geometry and query.get("sampling") == ["observed"]:
                            body = observed_cloud_payload(sel[1], rgb, step, geometry)
                        else:
                            body = cloud_payload(sel[1], rgb[::step, ::step], step=step, geometry=geometry)
                    except ValueError:
                        self._send(503, "text/plain", b"depth and colour grids differ"); return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Stereo-Pair", f"{sel[0][0]}:{sel[0][1]}")
                    self.send_header("X-Depth-Age-Ms", str((time.monotonic_ns()-capture_ns+999_999)//1_000_000))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._send(404, "text/plain", b"stereo is not enabled")
            elif p == "/api/mono_range":
                with _lock:
                    s = dict(_state)
                est = _est(s["raw"])
                age = int((time.monotonic() - s["t"]) * 1000) if s["t"] else None
                valid = range_valid(s["raw"], est, _engine["name"], age, s.get("lock_coasting", False))
                self._send(200, "application/json", json.dumps({
                    "class": s["class"], "raw": s["raw"] if valid else None, "est_m": est if valid else None,
                    "conf": s["conf"], "count": s["count"], "n_cal": len(_cal),
                    "fit": (_fit[0] if _fit else None), "valid": valid, "cal": _cal,
                    "detected": s["dets"], "age_ms": age}).encode())
            elif p == "/stream":
                kind = "clean" if parse_qs(urlparse(self.path).query).get("clean") == ["1"] else "annotated"
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame"); self.end_headers()
                with _lock:
                    _viewers["n"] += 1
                    _streams[kind] += 1
                try:
                    while True:
                        b = _jpg.get("clean" if kind == "clean" else "buf")
                        if b:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                                             str(len(b)).encode() + b"\r\n\r\n" + b + b"\r\n")
                        time.sleep(0.04)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with _lock:
                        _viewers["n"] = max(0, _viewers["n"] - 1)
                        _streams[kind] = max(0, _streams[kind] - 1)
            elif p == "/api/state":
                ok, after = parse_after(parse_qs(urlparse(self.path).query, keep_blank_values=True))
                if not ok:
                    self._send(400, "text/plain", b"bad cursor")
                else:
                    waited = None
                    if after is not None:               # hold the answer until a newer result is published
                        t0 = time.monotonic()
                        with _result:
                            _result.wait_for(lambda: _state.get("frame_seq") != after, timeout=WAIT_LIMIT_S)
                        waited = (time.monotonic() - t0) * 1000
                    snap = self._snapshot()
                    if waited is not None:
                        snap["waited_ms"] = waited      # lets the reader keep its age estimate conservative
                    self._send(200, "application/json", json.dumps(snap).encode())
            elif p == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
                with _lock:
                    _viewers["n"] += 1
                def write(snap):
                    self.wfile.write(b"data: " + json.dumps(snap).encode() + b"\n\n")
                    self.wfile.flush()
                def wait_changed(sent, timeout):         # next result as soon as it is published; 10 Hz heartbeat otherwise
                    with _result:
                        _result.wait_for(lambda: (_state.get("frame_epoch"), _state.get("frame_seq")) != sent, timeout=timeout)
                try:
                    event_stream(self._snapshot, write, wait_changed)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with _lock:
                        _viewers["n"] = max(0, _viewers["n"] - 1)
            else:
                self._send(404, "text/plain", b"nope")
        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/api/demo_face":
                value=q.get("enabled", [""])[0]
                if value not in ("0", "1"):
                    self._send(400, "application/json", b'{"ok":false}'); return
                self._send(200, "application/json", json.dumps(_faces.configure(value=="1")).encode())
            elif u.path == "/api/mount":          # bench only: a mount stated in the service environment cannot be changed here
                value = q.get("value", [""])[0]
                if value not in ("fixed", "vehicle"):
                    self._send(400, "application/json", b'{"ok":false,"reason":"value must be fixed or vehicle"}'); return
                if MOUNT_LOCKED and value != _mount["value"]:
                    self._send(409, "application/json", b'{"ok":false,"reason":"mount is set by the service configuration"}'); return
                with _lock:
                    _set_mount(value)
                self._send(200, "application/json", json.dumps({"ok": True, "mount": value}).encode())
            elif u.path == "/api/transmit":       # operator: where the on-vehicle command should be sent (off | sim | fc_hold | fc)
                try:
                    with _lock:
                        _transmit.ask(q.get("target", [""])[0], _mount["value"], time.monotonic())
                        out = _transmit.snapshot(time.monotonic())
                    self._send(200, "application/json", json.dumps({"ok": True, "transmit": out}).encode())
                except ValueError as e:
                    self._send(409, "application/json", json.dumps({"ok": False, "reason": str(e)}).encode())
            elif u.path == "/api/transmit_status":  # a sender's heartbeat: what it is doing with the command
                try:
                    with _lock:
                        gen = q.get("generation", [None])[0]
                        out = _transmit.report(q.get("target", [""])[0], q.get("state", [""])[0], q.get("reason", [""])[0], time.monotonic(),
                                               None if gen is None else int(gen), q.get("instance", [None])[0],
                                               {"1": True, "0": False, None: None}[q.get("stream", [None])[0]])
                    self._send(200, "application/json", json.dumps(dict(out, ok=True)).encode())
                except (ValueError, KeyError) as e:
                    self._send(400, "application/json", json.dumps({"ok": False, "reason": str(e)}).encode())
            elif u.path == "/api/onboard_record":  # carry-around: start a bounded numbers-only recording of the shadow command
                if _mount["value"] != "vehicle":
                    self._send(409, "application/json", b'{"ok":false,"reason":"camera mount is not vehicle"}'); return
                try:
                    path = _shadow.start(q.get("label", ["carry"])[0], float(q.get("seconds", ["120"])[0]))
                    self._send(200, "application/json", json.dumps({"ok": True, "path": path}).encode())
                except ValueError as e:
                    self._send(409, "application/json", json.dumps({"ok": False, "reason": str(e)}).encode())
            elif u.path == "/api/mono_target":
                with _lock:
                    _state["class"] = q.get("class", ["person"])[0] or "person"
                    _operator_select(None, False)                # explicit class-mode selection
                self._send(200, "application/json", b'{"ok":true}')
            elif u.path == "/api/mono_click":     # click (native px) -> lock the TRACK under/nearest it
                try:
                    cx, cy = float(q.get("x", ["0"])[0]), float(q.get("y", ["0"])[0])
                except ValueError:
                    self._send(400, "application/json", b'{"ok":false}'); return
                if not (-1e7 < cx < 1e7 and -1e7 < cy < 1e7):
                    self._send(400, "application/json", b'{"ok":false}'); return
                with _lock:
                    tf = list(_state["tracks_full"])
                picked, best_key = None, None
                for tid, lbl, x, y, w, h, *_ in tf:
                    inside = (x <= cx <= x + w) and (y <= cy <= y + h)
                    d2 = (x + w / 2 - cx) ** 2 + (y + h / 2 - cy) ** 2
                    key = (0, w * h) if inside else (1, d2)     # prefer a box under the click (smallest)
                    if best_key is None or key < best_key:
                        best_key, picked = key, (tid, lbl)
                if picked is not None:
                    with _lock:
                        _operator_select(picked[0], True); _state["class"] = picked[1]
                self._send(200, "application/json",
                           json.dumps({"ok": picked is not None, "class": picked[1] if picked else None}).encode())
            elif u.path == "/api/reid_record":    # bench: start a bounded identity recording; this machine only
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    self._send(403, "application/json", b'{"ok":false}'); return
                try:
                    path = _recorder.start(q.get("label", [""])[0], float(q.get("seconds", ["0"])[0]))
                    self._send(200, "application/json", json.dumps({"ok": True, "path": path}).encode())
                except ValueError as e:
                    self._send(409, "application/json", json.dumps({"ok": False, "reason": str(e)}).encode())
            elif u.path == "/api/select_track":   # exact track ID, checked and applied under one lock
                with _lock:                              # freshness, identity and the selection itself are one step
                    ok, body = select_by_id(_state, INSTANCE, q, time.monotonic())
                    if ok:
                        _operator_select(body["id"], True); _state["class"] = body["label"]
                self._send(200 if ok else 409, "application/json", json.dumps(body).encode())
            elif u.path == "/api/mono_calibrate":
                try:
                    dist = float(q.get("dist", ["0"])[0])
                except ValueError:
                    self._send(400, "application/json", b'{"ok":false}'); return
                with _lock:
                    raw = _state["raw"]
                if raw is None or dist <= 0:
                    self._send(200, "application/json", b'{"ok":false,"reason":"no_target"}'); return
                _cal.append((raw, dist)); _refit(); _save_cal()
                self._send(200, "application/json", json.dumps({"ok": True, "n_cal": len(_cal)}).encode())
            elif u.path == "/api/mono_calibrate_reset":
                _cal.clear(); _refit(); _save_cal()
                self._send(200, "application/json", b'{"ok":true}')
            elif u.path == "/api/depth_engine":
                eng = q.get("engine", [""])[0]
                if eng not in ("y26d", "da"):
                    self._send(400, "application/json", b'{"ok":false}'); return
                _engine["name"] = eng
                self._send(200, "application/json",
                           json.dumps({"ok": True, "engine": eng}).encode())
            elif u.path == "/api/detector":
                self._send(*self._set_detector(q))
            else:
                self._send(404, "text/plain", b"nope")

        def _set_detector(self, q):
            """POST /api/detector?mode=primary|world|pose&prompt=a&prompt=b — swap the live detector.

            ANY world/pose-path failure (bad prompt, CLIP embed, HEF configure, first infer) leaves
            the primary detector running and answers {"ok": false, "reason": ...}; the loop never
            sees it.
            """
            def bad(reason, code=200):
                return code, "application/json", json.dumps({"ok": False, "reason": reason}).encode()
            mode = q.get("mode", ["primary"])[0]
            if mode not in ("primary", "world", "pose"):
                return bad("mode must be primary|world|pose", 400)
            if mode == "primary":
                with _det_lock:
                    _detector.update(mode="primary", prompt=[], gen=_detector["gen"] + 1)
                return 200, "application/json", json.dumps(
                    {"ok": True, "mode": "primary", "prompt": []}).encode()
            if mode == "pose":
                try:
                    with _det_lock:
                        if _pose["det"] is None:
                            _pose["det"] = HailoYoloPose(POSE_HEF, vdevice=_vd["h"])
                        _detector.update(mode="pose", prompt=[], gen=_detector["gen"] + 1)
                        _gest["fsm"] = GestureFSM(); _gest["handoff_fsm"] = GestureFSM()
                        _gest["handoff_last_id"] = None
                        with _lock:
                            _state["class"] = "person"
                except Exception as e:
                    print("mono detector switch failed:", repr(e))
                    return bad(f"{type(e).__name__}: {e}")
                return 200, "application/json", json.dumps(
                    {"ok": True, "mode": "pose", "prompt": []}).encode()
            prompt = [w.strip() for w in q.get("prompt", []) if w.strip()]
            if not prompt:
                return bad("world mode needs at least one prompt")
            if len(prompt) > N_SLOTS:
                return bad(f"at most {N_SLOTS} prompts (the HEF has {N_SLOTS} fixed class slots)")
            try:
                # CLIP runs on the CPU and can take ~1 s — do it BEFORE taking _det_lock so the
                # perception loop keeps running while the prompt is embedded.
                emb, _bg = embed_prompts(prompt)
                with _det_lock:
                    with _lock:
                        img = _frame["img"]
                    # prompt + prove, atomically: on failure the detector keeps its old vocabulary
                    _activate_world(_vd["h"], prompt, emb, img)
                    _detector.update(mode="world", prompt=prompt, gen=_detector["gen"] + 1)
                    with _lock:
                        _state["class"] = prompt[0]; _operator_select(None, _lockid["pinned"])
            except Exception as e:
                print("mono detector switch failed:", repr(e))
                return bad(f"{type(e).__name__}: {e}")
            return 200, "application/json", json.dumps(
                {"ok": True, "mode": "world", "prompt": prompt}).encode()
    return H


def main():
    _load_cal_file()
    threading.Thread(target=capture_thread, daemon=True).start()
    vd = VDevice(VDevice.create_params()); _vd["h"] = vd
    det = HailoYolo26(YOLO, vdevice=vd, conf_thresh=CONF)
    engines = {} if SKIP_MONO_DEPTH else {"y26d": Yolo26Depth(Y26D_HEF, vdevice=vd), "da": DepthAnything(vd)}
    if REID_HEF:
        from hailo_osnet import HailoOsnet
        _reid["model"] = HailoOsnet(REID_HEF, vd)
        print("re-id embeddings enabled (EVAL-ONLY model):", REID_HEF)
    threading.Thread(target=loop, args=(det, engines), daemon=True).start()
    print("garuda-mono -> http://0.0.0.0:8098/")
    ThreadingHTTPServer(("0.0.0.0", 8098), make_handler()).serve_forever()


if __name__ == "__main__":
    main()
