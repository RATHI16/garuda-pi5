# Host-computed stereo: OAK is capture/encoding only

Owner changed the architecture on 2026-11-12: stereo matching must run on the Pi
or Hailo, not RVC2. The standing `--host-stereo` path contains only Camera nodes
and an optional VideoEncoder. It never instantiates StereoDepth, NeuralNetwork,
on-device rectification or IMU nodes. RGB JPEG preview is encoded on the CPU;
optional H.265 remains on-camera. IR stays zero.

## Standing Pi backend

`hosts/pi5/oak/host_capture.py` captures raw 1280x720 BGR and two 640x400 mono
images at requested 15 Hz. A bounded eight-frame history matches all three by
**device timestamp**, rejecting triples with more than 5 ms span. This is a
software pairing limit, not proof of physical synchronization. The selector waits up to 50 ms for late mates. RGB decode, undistortion and JPEG
publication run independently of a single stereo worker. Its pending slot keeps
only the latest triple; stale jobs are rejected at 500 ms. Selection skips, pending
overwrites, unconsumed history eviction and input sequence gaps are separate counters.
Both `/frame` and normal `/pair` advance without waiting for depth completion.

`host_stereo.py` uses the actual delivered-image SDK intrinsics/distortion and
factory camera extrinsics (requested explicitly in metres). OpenCV stereoRectify
and remap run on the Pi. StereoSGBM uses 96 disparities, block size 5, uniqueness
10, speckle filtering and 3-way mode, with two OpenCV threads. These are baseline
settings, not tuned acceptance thresholds. Factory calibration is read, never
modified. No new dependency was installed; existing OpenCV 4.10.0 and NumPy 2.2.4
are exposed to the isolated DepthAI 3.12.1 environment with
`PYTHONPATH=/usr/lib/python3/dist-packages` in the service drop-in only.

Disparity becomes 3D using Q; R1 is inverted to return to the original left-camera
frame; factory left-to-RGB extrinsics transform the points; they are projected
with the delivered RGB K. A nearest-Z buffer produces millimetres along the
undistorted RGB optical axis. No holes are filled. Consequently the 1280x720
aligned output is sparse; zero means unavailable, not zero distance. The 3D
coordinate convention is explicit, but physical calibration accuracy is still
unverified. `/pair` keeps its old framing and adds `depth_backend:pi-sgbm`;
calibration identity includes the backend configuration and source geometry.
The calibration GUI displays the backend name. New backend/epoch requires a new
capture session; old OAK-depth captures remain preserved.

## Deploy / revert

Run `hosts/pi5/oak/enable-host-stereo.sh` on the Pi from a complete staging directory.
It copies to a versioned directory, stops/restarts mono and OAK, validates startup,
and installs `30-garuda-host-stereo.conf`. It preserves the original owner and prior drop-ins. Default deployment does not
record pairs. Explicit recordings use a fresh UUID subdirectory on each start.
Startup failure or HUP/INT/TERM restores the prior configuration through EXIT
cleanup; SIGKILL/power loss cannot execute that cleanup. `--capture-only` installs an explicit RGB-only command. `--disable` is an alias
for capture-only: it no longer exposes an older RVC2 drop-in. Neither host stereo
nor on-camera depth runs in this mode. RGB returns to the original distorted
camera image; stereo calibration previews are unavailable until explicitly re-enabled. Calibration service also needs the updated geometry validator and
backend-label assets; `install-calibration.sh` installs these.

## Hailo experiment

The existing repository already authorized official StereoNet HEF evaluation as
**bench-only, not-product-cleared** (`models/stereonet_h10.md`). Vendor model YAML
says MIT, but the linked source lacks a licence and the downloaded pretrained ZIP
contains only ONNX. Keep the repository restriction; no product recipe or licence
clearance is inferred from the vendor metadata.

`stereonet_probe.py` uses the official Model Zoo v5.3.0 Hailo-10H HEF with runtime
5.3.0. SHA256:
`92769b50376f2aca5cf80e8e1ceebe8b55ba8888cd0be8b676c9170eca0b3f7a`.
It requests FLOAT32 output so HailoRT dequantizes. The old `hailo_stereo.py` returns
raw UINT8 codes; those must not be assumed to be pixel disparity without checking
quantization. Historical experiment values have not been recomputed or relabelled.

The probe centre-crops rectified 640x400 mono to a 640x191 band and resizes to
1232x368, replicates grayscale into three channels, restores output disparity to
the original band's resolution and divides it by horizontal resize scale. Outside
that band is unavailable. It tests both input orders. Agreement with CPU disparity
is not ground truth and model-positive predictions are not confidence estimates.

Two saved-image runs were executed: StereoNet alone and StereoNet plus YOLO26m
co-resident on one VDevice, called sequentially. This is **not** a live shared-NPU
service or a simultaneous asynchronous throughput benchmark. The camera continued
CPU stereo while these tests ran. Live `garuda-mono` was stopped for each probe and
restored with a shell EXIT trap. Do not start a second independent NPU owner for a
standing learned-depth service; integrate model scheduling in one owner first.

Remaining: measured board/tape validation, larger varied recordings, live shared
NPU integration if quality warrants it, and a full host-stereo + YOLO + H.265 load
test. No VIO, flight acceptance or metric accuracy claim follows from these tests.

## Live Hailo experiment (2026-11-12 follow-up)

`preview.py --host-stereo --stereo-hef /path/to/stereonet.hef` selects the explicit
bench-only `hailo_host_stereo.py` backend. It requests dequantized FLOAT32 disparity,
uses the central crop, rescales disparity with nearest-neighbour sampling and
projects on the Pi. No SGBM computation is used in this mode. Model hash/backend
settings enter the calibration identity; `/pair` and GUI say `hailo-stereonet`.
This mode requires exclusive NPU ownership. Use `run-hailo-stereo-check.sh HEF SECONDS`
for a transient test (maximum 600 seconds). The runner stops competing services,
declares systemd conflicts and restores CPU owner + mono on exit. Admission requires
`GARUDA_EXCLUSIVE_STEREO=1` and inactive detector services before opening Hailo or OAK.
This coordinates known services; it cannot exclude arbitrary noncooperating processes.
The new runner has local tests/syntax checks but has not run on hardware: thermal
limits blocked the follow-up.
Do not run it alongside the existing detector as two independent VDevice owners.

A 60-second live test completed; see benchlog and recorded JSON. YOLO was paused
for this test and restored afterwards, together with standing pi-sgbm. The earlier
saved-image shared-VDevice test does not make this live backend shared yet. No
persistent service selection was changed. A transient systemd service, stopped
with SIGINT under an EXIT restoration trap, hosted the test on the usual owner
port so the calibration GUI could display its output.

## Review corrections and measurement qualification

All current host depth is **unqualified preview**, including CPU SGBM. Sparse
projection and occlusion handling are not physically validated; StereoNet also lacks
validated prediction reliability. A positive disparity is not confidence. No torso
threshold was lowered to make sparse output pass.

Normal `/pair` returns current RGB with `depth_valid:false` and
`measurement_qualified:false`. Explicit loopback `/pair-preview` carries completed
RGB/depth pairs with `preview_only:true`; the calibration GUI opts in and labels
nonzero pixel coverage as coverage, not confidence. The standard parser rejects
unqualified depth unless its caller explicitly requests previews, and the estimator
also rejects it. Preview depth must not feed following or accuracy claims.

Loopback `/timing?after=ID` exposes a bounded 8192-event ring with cursor/overrun
reporting. Events identify capture, dequeue, RGB publish, stereo stages, completion
and depth publication. `/health` includes runtime versions and source hashes.
`bench/oak_flow_bench.py` collects this alongside system/per-process CPU counters,
RSS data, temperature and flags. It stops on current throttle flags. Filter initial
ring history to the measurement window before computing statistics.
`bench/instrument_oak_mono.py` creates a separate instrumented detector copy with
completed-inference counts and timestamps; it does not modify the deployed original.
The instrumentation is not persistently enabled in the standing demo.

The review-fix hardware run stopped after two samples on a thermal limit. It proves
functional independent publication only, not sustained FPS, latency or an improvement.
See the benchlog and `bench/results/2026-11-12-stereo-review-fixes/`.


### Thermal fallback

The standing deployment was changed to capture-only after the review found continued
active thermal limiting. Invoke the installer with no argument to explicitly restore
host stereo for a controlled session once cooling is ready. Capture-only preserves
OAK RGB and the existing Hailo detector, with no stereo worker, mono capture or depth
preview. No claim of cooling adequacy follows merely from removing this load.

## Bounded on-demand CPU depth preview

With the coordination lock held and the standing owner in capture-only mode, run
`hosts/pi5/oak/run-cpu-depth-session.sh 60` on the Pi from a complete staging copy.
Duration is 1–600 seconds, measured from supervisor start (camera initialization
uses part of it). `--stop` ends it early. This is an operator CLI, not a new GUI
button. The calibration page automatically sees the preview while the session runs.

A transient systemd unit conflicts with the capture owner; Hailo detection is not
stopped. The session refuses a known active stereo trial, requires temperature below
75 C with no active limiting flags, checks every second and exits at 78 C or any
current limiting flag (thermal-read failure also exits). These are conservative
bench policy limits, not measured safe operating guarantees. Systemd RuntimeMaxSec
is a backstop and ExecStopPost restores the persistent RGB-only owner after normal
expiry, manual stop or process failure. No persistent stereo configuration is written;
a reboot uses capture-only. Switching modes restarts the camera and changes RGB
undistortion/epoch, so tracks may reset. Physical depth remains unqualified.

Projection version 2 uses direct OpenCV fixed-size transforms instead of general
Nx3 matrix multiplication. Double-precision transformations, nearest-pixel rasterizing,
nearest-Z collisions and zero holes remain. This reduces transformation overhead;
it does not solve sparse coverage, cross-pixel occlusion or prediction reliability.
`bench/oak_projection_bench.py` compares the frozen old kernel with the new kernel
on recorded disparities. Explicit translation scale is required for the calibration
file, and any changed output pixel fails the run. Timings are isolated kernel timings,
not live detector/stereo throughput or accuracy measurements.

### Comparing the depth views

The inset, expanded inspector and 3D viewer report distinct pairs drawn in the last
four seconds. The age bound follows the paired RGB timestamp through the HTTP request
and decode. RGB detection has its own counter. In 3D the one-metre guide is at lens
height in RGB optical coordinates. Its lines overlay visible surfaces. Double-click
to recenter the orbit on the current scene.

On the bench PC, use a Python environment with Playwright and Chrome. Keep the same
scene, selection, fan placement and display settings for the whole comparison. Close
other viewers. Reserve the bench with the other operators. Use your own Pi hostname and a new output directory for each run.

```sh
read -r -p "Pi hostname or IP: " PI_HOST
python bench/oak_depth_views.py --pi "garuda@$PI_HOST" --base "http://$PI_HOST:8098" --seconds 30 --out /tmp/oak-views-01
```

The tool opens RGB, inset, expanded, Fine 3D and Full 3D in forward and reverse order.
Each cloud phase keeps the original RGB tab open. It records actual browser status,
detector completions, host depth completions, CPU, temperature and throttle flags.
Use both repeats, check that pairs appeared, and retain any partial run. A workload
that never produced a pair cannot establish the cost of a functioning view. Thermal
limits use the existing host stereo bench policy above.

For distance checks, use **Calibration → Camera → Depth at measured distances**.
Mount the board flat. Record the tape reference and measure each placement with
the board facing the camera. Capture near, middle and far setup views, then move
the board again and make independent validation captures. Compare the held-out
board medians and valid-pixel fractions with their tape readings. Record misses too.
Validating the selected-person readout also requires separate person recordings.
