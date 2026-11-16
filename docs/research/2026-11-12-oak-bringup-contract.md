# OAK reference sensor bring-up contract

Status update on 12 November 2026: connectivity restored and a 30-second first-light run completed. Camera continuity passed the short sanity check; IMU sequence gaps and absent PTP reference remain open. See the second same-day OAK entry in bench/benchlog.md. The contracts below remain proposed, not deployed. Owner scope ends after stereo/IMU, Hailo detection and encoded video run concurrently. Do not start VIO, SLAM, Gazebo modifications or real flight-controller configuration during this work order.

## Repository reconciliation

- The owner's PSU uncertainty is resolved historically by `feat/px4-gazebo-demo` at `941b80d`, `bench/benchlog.md`, entry **2026-08-10 — Power supply upgrade**: official 27 W supply, negotiated 5000 mA. This is not a current power measurement. Recheck the attached supply and throttling before OAK streaming.
- The same worktree records sustained thermal limiting on October 31. Current cooling remains unverified. An adequate PSU does not establish thermal headroom.
- Jazzy in Docker on Pi OS is already a repository decision, with `hosts/pi5/Dockerfile.ros-jazzy`. Retain this topology. Official Jazzy binary support includes Ubuntu 24.04 arm64; do not add Ubuntu apt repositories to Trixie. The live Pi installation is not yet verified.
- `CLAUDE.md` requires a PTP time domain and originally describes FC IMU input. OAK's IMU is an additional reference-sensor input, not proof of FC/PTP synchronization. Keep the product timing contract; measure and expose the OAK-device → host-monotonic → PTP/ROS mappings before use. Do not label device-relative timestamps PTP.
- `interfaces/garuda_interfaces/messages.py` uses a monotonic capture timestamp by default and a NED `OdometrySample`. ROS sensor and VIO messages need explicit adapters, not a silent interpretation change to that schema.
- Root `CLAUDE.md` hardware prose is older than the demo worktree and owner report. No stale hardware paragraph was silently rewritten. OAK possession/fixed focus is owner-reported; device identity is pending enumeration.

## Licence gate inventory

Source URLs, inspected source revisions where available, and SHA256 hashes are in [license-sources.json](../../bench/results/2026-11-12-oak-precheck/license-sources.json). These are top-level source checks, not an audit of transitive dependencies, firmware, downloaded weights or a distributable image. DepthAI 3.12.1 and NumPy 2.5.4 were subsequently installed in an isolated Pi venv for first light; no VIO/SLAM candidate was installed. Installed-wheel notices and hashes are in `bench/results/2026-11-12-oak-precheck/installed-licenses.json`.

| Component | Source licence | Gate disposition |
|---|---|---|
| depthai-core | MIT | Eligible for isolated bench setup; inspect pinned release and bundled components |
| depthai-python | MIT | Python binding needed for a venv; same release verification |
| depthai-ros | MIT | Candidate for Jazzy wrapper; select compatible ROS branch/version, not arbitrary default |
| OpenVINS | GPL-3.0 family | `not-product-cleared`; later bench evaluation only |
| VINS-Fusion | GPL-3.0 family | `not-product-cleared`; later bench evaluation only |
| ORB-SLAM3 | GPL-3.0 family | `not-product-cleared`; later bench evaluation only |
| Basalt upstream | BSD-3-Clause | Source licence verified; current selected Monado fork URL failed, recheck exact checkout before changes |
| RTAB-Map | BSD-3-Clause | Candidate; optional dependencies need their own audit |
| Ultralytics YOLO26 | AGPL-3.0 source repository | Existing evaluation model stays unchanged; exact model/artifact terms remain separate |
| XFeat | Apache-2.0 source repository | Candidate for later evaluation; establish exact weight provenance separately |
| Original Magic Leap SuperPoint | Restrictive noncommercial research terms | Blocked for commercial demo/product use unless separately licensed; do not install |

Do not infer GPL “only” versus “or later” from the generic licence body alone. Record the exact selected source-file headers when packaging. The bench GPL relaxation does not grant permission to use proprietary research-only material commercially. Unbuilt DepthAI core and NumPy recipe markers were added with first light. Their presence does not establish a working Yocto build or product licence clearance.

## Proposed sensor contract

This is an interface specification, not a running ROS graph. `sensor_backend:=oak|gazebo` is the proposed launch selector. Both backends must implement the same names, units, optical-axis conventions and calibration geometry. Physical acquisition always uses `use_sim_time:=false`; Gazebo consumers use `true` and `/clock` consistently. Never mix physical sensor timestamps into a simulated estimator epoch.

| Topic | Type | Frame | QoS |
|---|---|---|---|
| `/garuda/sensors/stereo/left/image_raw` | `sensor_msgs/Image`, mono8 | `oak_left_optical_frame` | best effort, volatile, keep last 5 |
| `/garuda/sensors/stereo/right/image_raw` | `sensor_msgs/Image`, mono8 | `oak_right_optical_frame` | best effort, volatile, keep last 5 |
| `/garuda/sensors/stereo/left/camera_info` | `sensor_msgs/CameraInfo` | `oak_left_optical_frame` | reliable, volatile, keep last 5; publish with images |
| `/garuda/sensors/stereo/right/camera_info` | `sensor_msgs/CameraInfo` | `oak_right_optical_frame` | same |
| `/garuda/sensors/rgb/image_raw` | `sensor_msgs/Image`, bgr8 | `oak_rgb_optical_frame` | best effort, volatile, keep last 2 |
| `/garuda/sensors/rgb/camera_info` | `sensor_msgs/CameraInfo` | `oak_rgb_optical_frame` | reliable, volatile, keep last 5 |
| `/garuda/sensors/imu/data_raw` | `sensor_msgs/Imu` | `oak_imu_frame` | best effort, volatile, keep last 100 |
| `/garuda/sensors/health` | `diagnostic_msgs/DiagnosticArray` | source identifiers in values | reliable, volatile, keep last 10 |

QoS depths are proposed settings, not measured optimum values. Validate publisher/subscriber compatibility and actual DDS drops. Best effort must not conceal losses: retain sensor sequence counters in acquisition logs and report gaps separately from application overwrite/drop counts.

Optical frames use right/down/forward; the body uses FLU. Static transforms must come from measured mounting and factory/calibrated sensor extrinsics. Do not publish guessed IMU extrinsics. CameraInfo must match the delivered image resolution, crop, distortion model and rectification state. Raw and rectified streams must not share misleading calibration: add explicit `image_rect` topics if rectification is introduced.

For raw IMU, publish acceleration in m/s² and gyro in rad/s with truthful covariance. Mark unavailable orientation with `orientation_covariance[0] = -1`. Do not invent synchronized accel/gyro values when device packets have distinct timestamps: retain both timestamps in raw logs; document interpolation and its time basis before exposing a combined sample to VIO.

Later VIO publishes continuous local `odom -> base_link`. SLAM owns `map -> odom` for visualization/planning. Only the continuous VIO estimate may enter the PX4 adapter. Loop closure must not cause an EKF2 pose jump. This work order does not implement that bridge.

## Measurement protocol

### Step 0 prechecks

Record `uname -a`, OS/Python versions, `hailortcli fw-control identify`, active services and deployment hashes. Read `vcgencmd get_throttled`, `vcgencmd measure_temp`, the negotiated-current device-tree property and USB topology. Read the current property as a big-endian integer, not ASCII. Record baseline and during-load health; old sticky throttle bits and newly active bits are different observations.

Use both `lsusb -t` and DepthAI's negotiated speed after the runtime pipeline opens. A USB3 socket or pre-boot enumeration is not sufficient proof. IR projector and flood remain explicitly off. Never flash bootloader, overwrite EEPROM calibration or enable neural inference on OAK for this test.

### Step 1 first light

Select a pinned DepthAI release with a compatible wheel after reading the actual Pi Python/architecture. Install only into an isolated environment. If the selected version cannot support that Python, propose an isolated supported container; do not replace system Python or add arbitrary vendor wheels to Hailo's environment.

Dump device identity, camera sockets/models, detected IMU, factory calibration and reported versions. Distinguish host SDK version, runtime firmware identity and bootloader version. If an API cannot report one, mark it unavailable rather than substitute another value. Record baseline units explicitly and distinguish nominal FOV from FOV derived for a delivered crop.

Stream left/right/IMU/RGB without inference. Log each sample's stream, sequence, device timestamp, host-synced timestamp, arrival monotonic timestamp and current clock-mapping status. Compute received FPS over measured elapsed time, per-stream sequence gaps, duplicates and out-of-order counts. Profile per-core CPU using `/proc/stat` interval deltas; also report acquisition-process usage. No historical number is a new measurement.

### Step 2 topic contract

Implement the table above only after the SDK and devices are verified. Keep one device-owning process. Fan out frames to ROS, detection and video; do not have independent processes compete to open the OAK. Use `ICaptureBackend` for transport-neutral frame metadata, with host-specific implementation outside `services/`. Preserve the original V4L2 path and an explicit rollback configuration.

### Step 3 time base

DepthAI distinguishes host-synchronized `getTimestamp()` from device-relative `getTimestampDevice()` and uses a monotonic host clock. This is not automatically ROS epoch time or PTP. [Luxonis device documentation](https://docs.luxonis.com/software-v3/depthai/depthai-components/device)

First locate the deployed `garuda-time` implementation/service and its clock export. No `services/garuda-time` directory exists in the inspected root or PX4 worktree. It may be represented by linuxptp services on the Pi; inspect before assuming absence or inventing an endpoint.

Log host monotonic/ROS/PTP mapping samples with bracket uncertainty. Report mapping offset and variation, delivery age and left/right timestamp skew separately. Arrival-minus-capture time is delivery latency, not proof of device clock accuracy. An independently timed optical/electrical event is needed to validate physical exposure alignment; vendor synchronization claims are not a bench result.

### Step 4 detection swap

Use the current deployed model hash, decoding and preprocessing unchanged. Convert OAK output into the existing image boundary and carry the correct acquisition time, source ID and reconnect epoch. Do not change model resolution or FPS silently to improve the comparison. Test the new path in isolation before selecting it in the live demo.

The July Phase 1 benchmark is a different model/input workload. Record its provenance for context, but perform a fresh webcam-versus-OAK comparison using the same YOLO26 model, scene, capture settings, active consumers and power/thermal state. The October 31 webcam sanity run lacked continuous thermal logging and is not a clean acceptance baseline.

### Step 5 video link

Requested settings: OAK encoder RGB 1080p, H.265 CBR 2–4 Mbps, keyframe interval 15–30 frames. Repeat with H.264. Confirm actual encoder API support for the selected SDK and disable B-frames if supported for low latency. Relay compressed data through parser/payloader to RTP/UDP without host re-encoding. Keep WiFi backpressure from blocking sensor acquisition; after encoded-data loss recover at a usable keyframe rather than treating arbitrary packet drops as harmless.

Record the actual WiFi interface/channel/frequency, route to QGC and receiver codec support. Ethernet delivery does not count as a 5 GHz WiFi test. Measure glass-to-glass using a visible changing timer/LED and a recording that observes source and receiver together. Software timestamps alone do not include sensor/display delay. If no optical measurement is available, label glass-to-glass **TBD-bench** and report transport measurements separately.

### Step 6 concurrent load

Run stereo/IMU acquisition, Hailo detection and encoded video together. Log actual rates, gaps, process/per-core CPU, memory, encoder bitrate, Pi/OAK temperature where exposed, throttling and USB resets. Confirm the same model and stream settings are used. Stop on power instability or active thermal limiting; retain the evidence and do not claim a sustainable performance pass. Stop and report at this stage; later VIO and flight work is outside scope.

## Resume inputs

Hardware work requires the Pi to be reachable and the OAK attached to the intended host. Exact device revision, USB speed, IMU, firmware, calibration, installed SDK, cooling, current power status and receiver route are all pending. The camera is now verified on the Pi over runtime USB3. The first-light bench entry supersedes earlier unread device fields. No boot setting, driver installation or system upgrade was performed.

## Geometry investigation lead (offline, after logger review)

The current [Luxonis ImgTransformations documentation](https://docs.luxonis.com/software-v3/depthai/depthai-components/messages/img_transformations/)
describes per-frame metadata recording transforms, source intrinsics, dimensions and
distortion. Its `getSourceIntrinsicMatrix()` is explicitly the **source** matrix;
that alone must not be called the delivered-image intrinsic matrix. Inspect the
installed 3.12.1 API and each delivered frame's transformation before deriving K.
The [coordinate-system documentation](https://docs.luxonis.com/software-v3/depthai/depthai-components/coordinate-systems)
distinguishes calibrated camera frames from housing frames and describes virtual-camera
extrinsics after image processing. This supports using actual frame metadata and a
verified housing transform, rather than assuming the enclosure face is the optical origin.

These are investigation leads from current documentation, not confirmation that the
bench firmware implements every API described there. No reference-axis field is changed
from `unverified`. Next geometry acquisition should save RGB/depth transformations keyed
by calibration ID and frame identity, then compare paired boundary overlays at centre
and corners. Frame metadata agreement is necessary but does not replace visual/physical
alignment evidence. Images remain in local evidence outside Git; only measured results,
configuration and metadata belong in the committed report.
