# OAK first light

Bench-only hardware capture harness for DepthAI 3.12.1, NumPy 2.5.4, Python 3.13
on the Pi. No inference, flight commands, calibration writes or firmware flashing.
Device runtime loading is performed by the SDK; IR outputs are explicitly disabled.

The first verified run is recorded in `bench/benchlog.md` on 12 November 2026.
This harness requires an OAK-D Pro with its cameras on CAM_A/B/C. It refuses USB2.
It records 30 seconds by default, with a 60-second maximum and active throttle stop.

Installation used an isolated venv; system packages and Hailo's environment were
unchanged:

```bash
python3 -m venv /home/garuda/.venvs/oak-bringup-3.12.1
/home/garuda/.venvs/oak-bringup-3.12.1/bin/python -m pip install --only-binary=:all: depthai==3.12.1 numpy==2.5.4
```

On this Pi normal-user USB access was denied. The initial bounded diagnostic used
`sudo -n` with the isolated interpreter; no persistent udev permission was changed.
Do not deploy a production root service from this example. Run with the normal user
after a suitably scoped USB access rule is reviewed and installed.

```bash
sudo -n /home/garuda/.venvs/oak-bringup-3.12.1/bin/python first_light.py \
  --seconds 30 --output /home/garuda/oak-bringup/new-run-name
```

The output directory must not exist. It contains summary, calibration, timestamp
and IMU samples, and first-frame PGM/PPM captures. Keep images local unless sharing
is intended. Rates based on sensor timestamps and wall-clock delivery rates are
reported separately. Startup is included in the measurement window. No warm-up or
steady-state claim is implied. First-frame file writes also affect host scheduling.

CPU is measured for the entire system per core and this acquisition process.
The existing mono service remains active; without its original webcam connected
this is not a concurrent Hailo-inference performance benchmark.

Sequence gaps in IMU report counters need investigation; they are not necessarily
USB packet loss. Paired IMU messages can have different per-sensor timestamps and
cadences. The initial run is NOT a VIO input-quality acceptance pass.

DepthAI host code is MIT, but its bundled firmware has Luxonis-hardware-only terms.
This environment is for the OAK reference sensor, not a firmware port to Hailo-15H.
The installed-wheel licence inventory is saved with the bench evidence.


## Browser preview

`preview.py --port 8096` serves an RGB MJPEG page with fullscreen and freshness
status. JPEG encoding runs on OAK. On the bench it uses the same isolated
interpreter and elevated USB access as first light. It is deployed as a transient
`garuda-oak-preview` service, not enabled on boot. Stop it with
`sudo systemctl stop garuda-oak-preview` before opening the OAK in another process.
The preview does not perform Hailo inference or implement the QGC H.265 link.


## OAK input in the original demo

The later owner-requested input swap supersedes the transient-root preview setup
above. `garuda-oak-preview.service` is now enabled as `User=garuda` using the two
scoped USB rules in this folder. It owns one OAK connection for both the preview
and a loopback-only `/frame` raw BGR endpoint. The existing Hailo process uses
`OakRGBBackend` in `.worktrees/px4-gazebo-demo/hosts/pi5/oak_capture.py`.
The `oak-camera.conf` systemd drop-in selects it. Model and preprocessing remain
unchanged; old camera calibration is kept separate. The original :8098/demo is
now the detection/tracking view; :8096 remains raw preview.

Rollback: remove only the `oak-camera.conf` drop-in, daemon-reload and restart
`garuda-mono`. Stop the preview before any independent OAK experiment. Camera
service restart-on-failure handles disconnects; no persistent frames should be
interpreted as fresh data. Stereo/IMU and QGC encoding are not active in this
RGB demonstration pipeline.

## Stereo owner candidate (requires cross-review before integration)

`preview.py` now adds on-device StereoDepth from 640×400 left/right at 15 fps,
aligned using the RGB output metadata, and `/pair` v1. RGB BGR and hardware MJPEG
remain 1280×720 at a requested 15 fps. RGB undistortion is explicitly enabled for
both outputs; this is a geometry change from the previous preview. Model input
shape is unchanged, but old monocular distance calibration is not transferable.
`pairing.py` must be installed beside the deployed script if this candidate is
accepted. No new Python dependency is required.

The implementation follows the RVC2 `inputAlignTo` path documented in the
[Luxonis Depth Align example](https://docs.luxonis.com/software-v3/depthai/examples/image_align/depth_align).
The installed SDK is still 3.12.1. Depth's numeric reference axis remains
`unverified`; pixel alignment does not itself validate Z semantics or metric
accuracy. Factory calibration + output geometry are hashed into `calibration_id`.

`/frame`, `/stream` and `/` remain available. `/pair` and detailed `/diagnostics`
are loopback only. `/health` publishes stream ages, received rates, observed
sequence gaps, bounded host-pairing queue overwrite counts and configured limits.
Sequence gaps are not labelled proven USB drops. SDK output queues are also bounded;
their overwrites cannot be separately inferred from these counters. IMU is explicitly
disabled in this candidate; its follow-up and video encoding are later work.

The new pair path bounds its depth wait at 80 ms and request wait at 2000 ms,
rejects acquisition age ≥1000 ms, and provisionally accepts device timestamp skew
≤33,333,333 ns. These are design settings, not validated navigation thresholds.
`depth=0` changes only the response, not camera computation. Missing/stale depth
never becomes a previous depth frame attached to a new RGB frame.

PC tests: `python3 -m pytest -q hosts/pi5/oak/test_pairing.py`.
Loopback smoke (owner running on alternate port, reviewed `oak_pair.py` importable):
`python3 hosts/pi5/oak/smoke_pair.py --base http://127.0.0.1:8097 --seconds 30 --out result.json`.
The smoke saves metadata and depth statistics only, not images. Do not run another
camera owner alongside the live service. Acquire the shared Pi lock, temporarily
stop both live services, use a transient test service with a runtime limit, and
restore both services in an EXIT trap. Production deployment waits for cross-review.

### Review follow-up: explicit geometry opt-in

The owner now defaults to RGB-only capture. `--stereo` explicitly enables stereo
and undistorted RGB for `/frame`, `/pair` and MJPEG together. Without the flag,
`/pair` returns 503 and no stereo pipeline is created. Installing the candidate
alone therefore does not opt the detector into the new geometry. An integrated
stereo test must select BOTH producer `--stereo` and consumer
`GARUDA_MONO_OAK_PAIR=1`; rollback must remove BOTH choices. Consumer rollback alone
cannot undo the producer geometry change.

The previous smoke evidence predates this flag: repeat its transient owner command
with `--stereo`. Current follow-up is PC-tested only. Depth-edge alignment checks at
centre/corners, delivered intrinsics and the Z reference axis remain open before
metric tape testing. Do not substitute scaled factory intrinsics and call them
verified delivered-image intrinsics. No new camera images have been saved by this
follow-up.
