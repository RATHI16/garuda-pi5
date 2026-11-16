# OAK-D Pro bring-up — session handoff prompt (2026-11-12)

> **PARTLY SUPERSEDED the same day (2026-11-12, consolidation).** This prompt was written before
> a parallel Codex session ("Astra") completed pre-checks, first light, a browser preview and the
> detection input swap. Read `docs/oak-coordination.md` and the 2026-11-12 entries in
> `bench/benchlog.md` first. The body below is kept unchanged as history. Corrections:
>
> - **OAK is verified**, not owner-reported: OAK-D-PRO-FF, IMX378 + 2× OV9282 + BNO086,
>   calibration saved, short first-light record exists. The bootloader API returned `None`;
>   no bootloader version is known.
> - **USB:** the unbooted device enumerates at 480M; at runtime the SDK reports `SUPER`. A 480M
>   line in `lsusb -t` before boot does **not** mean a bad port or cable (§3 step 0 is wrong here).
> - **Architecture (§2 is superseded):** the OAK is owned by a separate non-root service,
>   `garuda-oak-preview`, which serves raw frames to `garuda-mono` over loopback. There is no
>   `_run_oak()` inside `garuda_mono.py`; the live source is `oak-rgb` via `OakRGBBackend`.
> - **Venv:** DepthAI 3.12.1 lives in its own isolated venv
>   `/home/garuda/.venvs/oak-bringup-3.12.1`. Hailo keeps using system Python. No
>   `--system-site-packages` venv and no `ExecStart` override of `garuda-mono`.
> - **udev:** scoped rules (two product IDs, group `garuda`, mode 0660) are installed. The
>   world-writable 0666 rule suggested in §3 step 0 is superseded — do not install it.
> - **IR:** the code explicitly sets both IR intensities to zero. "Never call" the intensity
>   methods (§2) should read "never set them above zero".
> - **Firmware licence:** the installed wheel contains written Luxonis-hardware-only firmware
>   terms, so "no written licence anywhere" (§1) is superseded for that bundled runtime.
>   Bootloader and product-image redistribution questions remain open.
> - **fps method:** `/stream` repeats the latest JPEG; counting JPEG frames overstates the
>   inference rate (§3 step 4). Count distinct processed frames or source timestamps.
> - **Depth:** the demo's distance is still the monocular YOLO26 estimate. Nothing measured so
>   far is OAK stereo depth.
> - **Scope:** PTP and ROS/Docker installs are deferred until after the final concurrent test
>   review (owner, 2026-11-12). Steps 2 and 3 below remain open, not passed.
> - **Not installed on the Pi** (checked read-only 2026-11-12): `docker`, `gst-launch-1.0`, `ptp4l`.

This file is a prompt for a fresh Claude Code session. Paste or point the session at it.
It records what is true as of 2026-11-12 ~14:00 IST, what is assumed, what is blocked,
and the exact work order. Everything in `CLAUDE.md` applies (numbers sacred, no casual
upgrades, licence gate, hardware safety, rule 10 for sudo/boot changes). **Where this file
and the repo disagree, the repo wins — report the conflict, don't resolve it silently.**

Work in the worktree **`.worktrees/oak-d-pro`** on branch **`feat/oak-d-pro`** (created
2026-11-12 from `feat/px4-gazebo-demo` @ `941b80d`). Do not `cd` to the main checkout.
Shell cwd resets between tool calls in this repo — use absolute paths.

---

## 1. Current state (verified unless marked)

### Bench hardware
| Item | State |
|---|---|
| Pi 5 8 GB + AI HAT+ 2 (Hailo-10H) | `garuda@10.42.0.128`, passwordless sudo. Kernel `6.18.34+rpt-rpi-2712`, Python 3.13.5, HailoRT/fw/driver/binding **5.3.0**, OpenCV 4.10.0. |
| Power | **Official 27 W PSU installed + rail-verified 2026-08-10** (benchlog: 5000 mA contract, EXT5V 5.17 V, `0x0` under mono load). The brief's "27 W PSU item still open" is **wrong — closed**. |
| Cooling | **Passive heatsink only.** Throttles after ~30 min of continuous detection: `get_throttled=0x80008` at 82–83 °C observed 2026-10-31 and 2026-11-01. Numbers taken while throttled are tainted. Active cooler purchase still pending as far as the repo knows. |
| Camera Module 3 (CSI) | **Electrically dead on both CSI ports since 2026-10-24** (firmware `BSC no ACK`, imx708/dw9807 probe −5/−121, full I2C scan silent; Pi-side rp1-cfe healthy). Cable or module; swap pending. |
| USB webcam (Lenovo FHD, UVC) | On `/dev/video0`, 1280×720 MJPG — current detection source. |
| **OAK-D Pro (fixed focus)** | **Owner reports (2026-11-12): physically connected and working. NOT yet verified by any session** — the Pi was unreachable at last check (no ethernet carrier, no hotspot → powered off). Verify first (§3 step 0). |
| Hailo-15L SBC | Separate device, `ssh root@10.0.0.1` via PC profile `h15-link`. Not part of this work. |

### Networking / access gotchas
- PC `eno1` has two autoconnect profiles: `pi-share` (Pi, shared → Pi gets 10.42.0.128) and
  `h15-link` (15L, static 10.0.0.2). `pi-share` has autoconnect-priority 10, but if the Pi
  shows up on the wrong one: `nmcli con up pi-share`. Check `cat /sys/class/net/eno1/carrier`.
- Pi WiFi AP `GarudaBench` (5 GHz ch36, Pi = 10.43.0.1). Its visibility in `nmcli dev wifi list`
  is a cheap "is the Pi powered" probe.
- SSH with `-o BatchMode=yes`. Some remote `sudo` writes (e.g. `shutdown`) get blocked by the
  permission classifier; hand the owner a short one-liner to run with the `!` prefix.
- CSI is NOT hot-pluggable. OAK is USB — hot-plug is fine, but use a **USB 3 (blue) port** and a
  USB 3 cable; confirm `5000M` in `lsusb -t`.

### Software state on the Pi
- `garuda-mono` systemd service: `/usr/bin/python3 -u /home/garuda/garuda-sdk-deploy/hosts/pi5/garuda_mono.py`,
  GUI `:8098/`, booth console `:8098/demo`, SSE `/api/events`, snapshot `/api/state`,
  MJPEG `/stream`. Drop-in `depth.conf` sets `GARUDA_MONO_DEPTH=y26d`.
- Capture auto-selection (commits `515421d`, `158dfff`): env `GARUDA_MONO_CAM` = `auto`
  (default: CSI via `rpicam-vid` first, else first USB UVC node) | `csi` | `/dev/videoN`.
  Functions `capture_thread()`, `_run_csi()`, `_run_v4l2(dev)`, `_usb_cams()` in
  `hosts/pi5/garuda_mono.py`. Frame contract: `_frame = {"img", "seq", "t", "source", "epoch"}`.
- Deploy = `scp` the changed files to `/home/garuda/garuda-sdk-deploy/hosts/pi5/` then
  `sudo systemctl restart garuda-mono`. Deployed copies were byte-identical to
  `feat/portal-tracking` @ `99db1c5` on 2026-11-01 — re-check `md5sum` before editing.
- Tests: `cd hosts/pi5 && python3 -m pytest -q` → **84 passed, 3 skipped** (2026-11-12 baseline
  in this worktree). `pytest services/` does not collect on this PC (needs `cv2`) — pre-existing.
- `hosts/pi5/Dockerfile.ros-jazzy` exists (ROS 2 Jazzy in Docker was decided 2026-07-29). Whether
  Docker is installed on the Pi and whether the image still builds on Trixie is **unverified**.
- Time: no `services/garuda-time`. P3 has one measurement (benchlog 2026-07-29): ptp4l
  transient unit `garuda-ptp4l-client`, PC software-timestamped GM, 3.60 µs RMS; RP1 quirk
  needs `--hwts_filter=full`. PTP discipline is **not running persistently**.
- PC: `hosts/pc/` flight lab (PX4 SITL + Gazebo Harmonic + ROS 2 Jazzy via `pixi`), see
  `docs/DEMO_USER_GUIDE.md`. GitHub `bhanuprakashjh/garuda-pi5` is **public**, `main` @ `32917bd`.

### Licence gate — DONE 2026-11-12 (verified at source)
Full report: `docs/dependencies/2026-11-12-oak-vio-licence-review.md`. Roll-up:
- **SHIP-OK:** depthai-core 3.12.1 / depthai-python 2.33 / depthai-ros (MIT); Basalt (BSD-3,
  `thirdparty/` still to audit); RTAB-Map core + rtabmap_ros (BSD-3, **only with a pinned CMake
  option allowlist** — many `WITH_*` options pull GPL / CC-BY-NC); XFeat (Apache-2.0, code +
  weights); GStreamer core/-good incl. `rtph264pay`/`rtph265pay` (LGPL-2.1, dynamic).
- **EVAL-ONLY `not-product-cleared`:** OpenVINS, VINS-Fusion (file spelled `LICENCE`), ORB-SLAM3
  (all GPLv3); Ultralytics code **and all YOLO26/v8 weights / derived HEFs** (AGPL-3.0);
  GStreamer GPL-linked encoders (`x265enc`) and -ugly.
- **RESEARCH-ONLY (correction to the brief):** Magic Leap SuperPoint **code and weights** —
  academic/non-profit/individual licensee only. Corporate bench use doubtful. Use XFeat.
- **UNCLEAR:** OAK device **firmware + bootloader binaries** — closed source, no written
  licence/EULA found anywhere. Fine as a purchased peripheral; **redistributing them in a
  product image needs written terms from Luxonis** (owner action before Phase 8). Luxonis model
  zoo is per-model (`model.yml` / HubAI licence field).
- **Still owed under rule 6 when the dependency lands:** skeleton bitbake recipes under
  `yocto/meta-garuda/` for depthai-core (and gstreamer plugins if used): name/version/LICENSE/SRC_URI
  stubs, unbuilt is fine.

### Conflicts between the owner's brief and the repo (repo wins — report, don't resolve)
1. "27 W PSU still open" → closed since 2026-08-10. The real open power item is **cooling**.
2. "Measure OAK time against `garuda-time`" → no such service; measure against the Pi system
   clock and state that PTP discipline is not persistent.
3. "Compare fps with the Phase 1 baseline" → P1's 75.95 fps was yolov8m on a sample video.
   Honest comparators: mono pipeline ~10 fps ceiling (benchlog 2026-08-10) and the webcam
   24.8 fps `/stream` sample (2026-10-31, un-throttled; 7.8 fps later the same day while throttled).
4. "ROS 2 on Trixie probably needs a container — verify, then propose" → already decided
   (Jazzy in Docker, 2026-07-29) and a Dockerfile exists; the task is "verify it still builds".
5. "SuperPoint weights research-only" → code too (see licence gate).

---

## 2. Design decisions to carry (made 2026-11-12 in this handoff, open to owner override)
- **One process owns the OAK.** DepthAI allows a single host process per device. Steps 4–6
  therefore run **one combined pipeline** (mono L/R + IMU + RGB ISP → host, RGB → on-device
  VideoEncoder → host) inside one owner process, enabled by flags; the stereo/IMU stats harness
  and the encoder bitstream writer are threads/sub-outputs of that process, not separate device
  openers. For steps 4–6 the simplest owner is `garuda_mono.py` itself (`_run_oak()`), with
  stereo/IMU/encoder as optional side outputs. The future ROS bridge takes the owner role later.
- **DepthAI install = venv with `--system-site-packages`** at `/home/garuda/oak-venv` (so
  `hailo_platform` stays importable) + a systemd drop-in `oak.conf` overriding `ExecStart` to the
  venv python. No system pip, no apt upgrades. **Check first** that a `cp313` `aarch64` wheel
  exists for the chosen depthai version (PyPI JSON: `https://pypi.org/pypi/depthai/json`,
  look for `cp313` + `manylinux…aarch64`). Prefer depthai **v3.x** (3.12.1 is current, bindings
  inside depthai-core); fall back to v2.33 if only that has cp313; if neither → Docker container
  with the official image, and say so. Pin the version in `bench/versions.md`.
- **Auto-selection order becomes `csi → oak → usb`** (`GARUDA_MONO_CAM=oak` to force). Extract
  the ordering into a pure function in `mono_state.py` and TDD it.
- **IR projector and flood LED stay off** — never call `setIrLaserDotProjectorIntensity` /
  `setIrFloodLightIntensity`.
- **Video link stays LGPL:** OAK on-device H.265/H.264 encoder → `fdsrc ! h265parse !
  rtph265pay config-interval=1 pt=96 ! udpsink host=<QGC> port=5600`. No `x265enc`/`x264enc`.
- Keep rectify/resize/crop behind one interface (OpenCV now, Hailo-15H DSP later).

---

## 3. Work order (each step ends with a benchlog entry: what changed, what was measured + the
exact command, what is assumed, what is blocked)

### Step 0 — Pre-checks (no code)
```bash
ssh -o BatchMode=yes garuda@10.42.0.128 'uptime -p; vcgencmd get_throttled; vcgencmd measure_temp; \
  cat /proc/device-tree/chosen/power/max_current | od -An -tu4; lsusb; lsusb -t; \
  which docker gst-launch-1.0; gst-inspect-1.0 rtph265pay >/dev/null && echo rtph265pay-ok; \
  ls /etc/udev/rules.d | grep -i movidius || echo no-udev-rule'
```
- Expect an `03e7:` device (Intel Movidius — unbooted MyriadX shows `03e7:2485`; after DepthAI
  boots it, `03e7:f63b`) at **`5000M`** in `lsusb -t`. `480M` = USB 2 → wrong port/cable; stop.
- If no udev rule: propose (rule 10) `/etc/udev/rules.d/80-movidius.rules` with
  `SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"` then `sudo udevadm control --reload-rules && sudo udevadm trigger`.
- `get_throttled` must be `0x0` at start; log it again during every streaming measurement.
- Record `bench/versions.md` entry (Pi kernel/python/hailo + depthai version + OAK fw/bootloader).

### Step 1 — First light
- Install depthai in the venv (see §2). Harness `bench/oak_first_light.py` (new; pure stats in
  `bench/oak_stats.py` with `bench/test_oak_stats.py`, TDD):
  - Read + record: `getDeviceName()`, USB speed (`getUsbSpeed()` must be `SUPER`), bootloader
    version, device firmware version, **IMU type** (`getConnectedIMU()` — BNO086 vs BMI270) and IMU
    firmware, `readCalibration()` → baseline (`getBaselineDistance`), per-camera intrinsics
    (`getCameraIntrinsics`), FOV (`getFov`), distortion, stereo rectification, camera→IMU extrinsics.
  - Stream **left + right mono (1280×800 or 640×400, record which), IMU (accel+gyro, 200/400 Hz,
    record which) and RGB 1080p** for ≥60 s. Per stream: delivered fps, dropped frames (sequence
    gaps), host-arrival inter-frame jitter, device-vs-host timestamp offset stats; per-core CPU
    from `/proc/stat` sampled at 1 Hz; temperature + `get_throttled` before/after.
  - Write raw JSON under `bench/results/<date>-oak-first-light/` (git-ignored by pattern —
    re-include the compact summary explicitly as the PC lab did) and the numbers into benchlog.

### Step 2 — Topic contract
- Write `docs/oak-topic-contract.md`: topics `/garuda/oak/{left,right}/image_raw` (+ `camera_info`,
  mono8, factory intrinsics/rectification), `/garuda/oak/rgb/image_raw` (or `/compressed`),
  `/garuda/oak/imu` (`sensor_msgs/Imu`); frame_ids `oak_left_optical_frame`,
  `oak_right_optical_frame`, `oak_rgb_optical_frame`, `oak_imu_frame`; static TF from factory
  calibration; QoS: images best-effort keep-last 5, `camera_info` transient-local, IMU best-effort
  keep-last 50; all stamps on the OAK device timebase mapped to host. The Gazebo model emits the
  identical contract; `sensor:=oak|gazebo` is the one launch argument.
- Verify (don't re-decide) the container: does Docker exist on the Pi, does
  `hosts/pi5/Dockerfile.ros-jazzy` build on Trixie, can the container see the OAK
  (`-v /dev/bus/usb:/dev/bus/usb --device-cgroup-rule='c 189:* rmw'`). Propose depthai-ros
  (MIT, `v3.x-jazzy`) inside it **or** a thin native publisher — keep ROS thin per CLAUDE.md §3.

### Step 3 — Time base
- Measure, for ≥10 min: `host_arrival − frame.getTimestamp()` (latency, per stream) and the
  drift of `getTimestampDevice()` vs host monotonic (offset mean / RMS / p99). State that the
  reference is the Pi system clock with PTP **not** disciplined (conflict 2). If ptp4l is
  re-enabled for the test (`--hwts_filter=full`, PC GM software-timestamped), say so and quote
  the 2026-07-29 caveats.

### Step 4 — Detection input swap
- `_run_oak()` in `garuda_mono.py`: ColorCamera 1080p → ISP scale to 1280×720
  (`setIspScale(2,3)`) → host BGR (`getCvFrame()`) → `_frame` (source `"oak"`). Model and
  post-processing untouched. Add `oak` to the auto order (TDD the pure selector). Unit drop-in
  `oak.conf` → venv `ExecStart`. Deploy, restart, confirm journal `capture: oak`.
- Measure with the benchlog 2026-10-31 method (`timeout 6 curl -s :8098/stream`, count JPEG SOI,
  decode first frame) **and** the loop `stats.fps` from `/api/state`; compare against the
  comparators in conflict 3. `get_throttled` before/after; if non-zero the number is tainted.

### Step 5 — Video link
- Same pipeline: RGB 1080p30 → `VideoEncoder` H.265 (RVC2 supports H.265 Main), CBR
  `setBitrateKbps(2000…4000)`, `setRateControlMode(CBR)`, `setKeyframeFrequency(15…30)`;
  bitstream → gst `fdsrc ! h265parse ! rtph265pay … ! udpsink port=5600`. If `rtph265pay` is
  missing on the Pi, propose `sudo apt install gstreamer1.0-plugins-good gstreamer1.0-tools`
  (package install, not an upgrade — state it per rule 10).
- QGC: Application Settings → Video → source "H.265 Video Stream", UDP port 5600 (H.264 variant
  for the repeat). PC address: over ethernet first (10.42.0.1); then over **5 GHz WiFi** — the
  Pi's own `GarudaBench` AP (PC joins it, PC loses internet for the test; PC = 10.43.0.x). Record
  which path each number came from.
- Glass-to-glass latency: camera pointed at a millisecond stopwatch on a screen; one photo
  captures the stopwatch and the QGC window; ≥10 samples per codec/bitrate; report mean + spread.
  Also record actual bitrate and dropped packets/frames.

### Step 6 — All three together
- Stereo + IMU streaming, detection on OAK RGB, and the encoder to QGC concurrently for ≥10 min
  from the one owner process. Record per-stream fps/drops, per-core CPU, temperature trace,
  `get_throttled` before/during/after. **If the active cooler is not fitted, expect throttling and
  mark every number tainted** (conflict 1). Then **stop and report** — do not start §4.

---

## 4. Deliverable: benchlog status block (append to `bench/benchlog.md`)
```
## <date> — OAK-D Pro bring-up status
What changed: <files/commits, depthai version, unit drop-ins>
What was measured: <table per step; each row with the exact command used + throttled state>
Assumed / not yet measured: <list, each marked TBD-bench>
Blocked: <list with the blocker and who can unblock it>
Conflicts with the brief: <the five above + any new ones>
```
Plus a `bench/versions.md` entry and `docs/decisions.md` entries for every decision in §2 that
survives contact with hardware.

---

## 5. Later phases (DO NOT start; design so they fit)
- Kalibr camera-IMU extrinsics + time offset vs factory values.
- Handheld open-loop VIO: closed walk returns to start, <1 % of distance travelled.
- Gazebo: PX4 `gz_x500_vision` first (EKF2 vision fusion, GPS off), then `gz_x500_depth` extended
  with a stereo pair + IMU matching the device calibration, `use_sim_time`, ground-truth scoring,
  fault injection.
- PX4 link: `/fmu/in/vehicle_visual_odometry`, ENU/FLU → NED/FRD, `EKF2_EV_CTRL`,
  `EKF2_EV_DELAY` from measured latency. Only VIO odometry → EKF2; SLAM loop closures never.
- Hailo assist for VIO, staged, each kept only if it beats the classical CPU baseline on measured
  error: (a) drop features inside YOLO boxes, (b) place recognition for loop closure,
  (c) learned features (XFeat — Apache; **not** Magic Leap SuperPoint). Classical front end stays
  as fallback.
- Hailo-15H portability: one interface for rectify/resize/crop (OpenCV now, DSP later); YOLO26 on
  greyscale input.

## 6. First three actions for the new session
1. `cat /sys/class/net/eno1/carrier`; if `1` ensure `pi-share`; ping `10.42.0.128`. If the Pi is
   off, stop and ask the owner to power it and plug ethernet + the OAK (USB 3).
2. Run the step-0 command block; confirm `03e7:` at `5000M` and `get_throttled=0x0`.
3. Check the depthai cp313/aarch64 wheel on PyPI before installing anything.
