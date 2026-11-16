# OAK completion and validation guide

This is the remaining bench workflow, not a flight-readiness claim. Original OAK RGB demo
and original deployed files remain the rollback. Takeover code `0a095ed` has now run on the Pi with stereo, IMU and hardware H.264/H.265.
See the dated benchlog and evidence for measured scope; physical geometry and distance
accuracy remain unverified. Do not confuse functional checks with accuracy validation. The single owner service alone opens the OAK; Hailo inference stays in system Python.

## Current integration

`hosts/pi5/oak/preview.py --stereo` retains existing paired depth behavior and publishes exact
SDK delivered-image K. `--imu` adds requested 400 Hz raw accelerometer/gyro, batch threshold 5,
maximum 40 reports, host queue 64 messages. `/imu` is loopback-only with epoch and cursor,
a 4096-report ring and explicit cursor-overrun reporting. Actual sensor delivery rates and
intervals must be measured; requested rates are not a performance claim.

Video is opt-in: `--video-codec h265 --video-host <receiver-IP> --video-port 5600
--video-kbps 3000`. Encoder is on OAK: 1920×1080, requested 15 fps, CBR, no B frames,
keyframe interval 15. H.264 is selected with `--video-codec h264`. This requests another
RGB output alongside the detection stream; benchmark concurrent cost. IR stays off.

Relay uses an external GStreamer parser/payloader subprocess. Four encoded frames maximum
wait in a host queue. Overflow or a stalled pipe restarts the relay and waits for a new
random-access frame with codec parameters. Diagnostics expose drops/restarts/errors. RTP
uses payloader/host arrival timing, NOT independently validated acquisition time/PTP.

## Dependencies: inspect before installing

No upgrade of Hailo, kernel, Python or DepthAI is required. Retain the isolated
`/home/garuda/.venvs/oak-bringup-3.12.1` SDK environment. Verify runtime USB3 and power flags.
For relay, inspect these elements on the Pi:

```sh
gst-inspect-1.0 fdsrc
gst-inspect-1.0 h265parse
gst-inspect-1.0 rtph265pay
gst-inspect-1.0 h264parse
gst-inspect-1.0 rtph264pay
gst-inspect-1.0 udpsink
```

If absent, first simulate installation and inspect package changes:

```sh
sudo apt-get -s install --no-install-recommends gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

After checking that the transaction doesn't alter the pinned Hailo/kernel stack, use the same
command without `-s`. No `apt upgrade`. Record installed versions and each element's plugin
license from `gst-inspect-1.0`. Core/GStreamer-owned plugins are LGPL-2.1; this is an external
executable relay, no static linking. Package bundles may have additional components: audit
actual dependency notices before distribution. No host software encoding is used.

Sources: [GStreamer licensing](https://gstreamer.freedesktop.org/documentation/frequently-asked-questions/licensing.html),
[Luxonis encoder API](https://docs.luxonis.com/software-v3/depthai/depthai-components/nodes/video_encoder).
Unbuilt Yocto dependency marker: `yocto/meta-garuda/recipes-multimedia/gstreamer/`.

## Temporary session and rollback

Obtain exclusive Pi ownership. Do not remove another session's lock while its restoration
trap can still execute. Stage complete `hosts/pi5`, `interfaces`, and `bench` from one pinned
commit. Include the new `hosts/pi5/oak/geometry.py`; the pair parser imports it.
Use runtime `/run/systemd/system/...service.d/` overrides, keeping original deployed files
and `/etc` units intact. The producer must use the isolated Python; consumer system Python.
On exit, stop the temporary units, remove only your override files, daemon-reload and restart
original units. Record original/after source and HEF hashes, ExecStart, active status,
temperature and both current/historical throttle bits. Don't remove another session's overrides.

## Receiver and link verification

For an independent decoded receiver on PC (change H265/h265 to H264/h264 for repeat):

```sh
gst-launch-1.0 -v udpsrc port=5600 caps='application/x-rtp,media=video,encoding-name=H265,clock-rate=90000,payload=96' ! rtpjitterbuffer latency=100 ! rtph265depay ! h265parse ! avdec_h265 ! fpsdisplaysink video-sink=fakesink text-overlay=false sync=false
```

In QGC Application Settings → Video (or General → Video in older builds), select
UDP h.265 Video Stream / UDP h.264 Video Stream to match the sender and set port 5600.
For a grounded bench, ensure "Disabled When Disarmed" does not hide the feed; do not arm
anything just to display video. See [official QGC settings](https://docs.qgroundcontrol.com/Stable_V5.0/en/qgc-user-guide/settings_view/general.html).

Decoding/fps measurement here is on the PC. Do not run QGC on the same UDP port concurrently
unless the receiver arrangement explicitly supports it. QGC reception must be verified in
its actual installed build; successful GStreamer decoding alone is not a QGC pass.

Check `ip route get <receiver-IP>` and WiFi frequency on the Pi. A working Ethernet route
is only a bench transport test; it does not satisfy the requested 5 GHz WiFi video link.
Do not change the bench networking implicitly. Glass-to-glass latency needs a timed visible
source and recording of source and receiver together. Packet/host timing is not a substitute.

## Measurement order

1. Geometry: capture exact RGB/depth pairs, numeric transformations/extrinsics, delivered K,
   centre/corner boundary overlays and configuration ID. Physical axis/origin still unverified.
2. Board: rigid flat textured backing; exclude edges/background from ROI. Save raw `/pair`
   locally, then run `python3 bench/oak_board_analysis.py pair.bin --roi x0 y0 x1 y1 --out report.json`.
   Both Z and radial hypotheses use the same pixels, exact K and robust plane procedure.
   Repeat positions and tilts. Results support hypotheses; do not automatically verify geometry.
3. Person tape test: declare physical tape reference and uncertainty; separate tuning and
   validation recordings. `oak_tape.py fit` is empirical scale/bias, not camera-origin proof.
4. IMU: owner with `--imu`, demo stopped first. Run `bench/oak_imu_record.py --seconds 120 --out
   <new-directory>` on Pi, then `bench/oak_imu_analysis.py <directory>/samples.jsonl --requested-hz
   400 --out <analysis.json>`. Report startup separately and cursor overruns/HTTP errors.
5. Video: repeat H.265 and H.264, receiver decode, actual route, bandwidth and optical latency.
6. Concurrent: at least 10 minutes of stereo + IMU + Hailo detection + encoded video. Run
   `bench/oak_soak.py run --minutes 10 --hold-stream --owner-base http://127.0.0.1:8096 --out
   <new-directory>` and the IMU recorder together. Declare browser count/cloud detail/viewers.
   Save receiver decode evidence and before/after source/model hashes. Active limiting means
   no sustained-performance pass; keep the failed trace. Stop and report at this boundary.

VIO/SLAM, camera–IMU calibration and PX4 odometry remain later phases. No motors or real FC
settings are touched by this workflow.


## QGC bench receiver configuration actually exercised

QGroundControl v5.0.8 native AppImage was started with a separate XDG_CONFIG_HOME,
leaving the owner's normal settings untouched. Application Settings → Video → UDP H.265,
UDP URL `0.0.0.0:15601`. The PC receiver below forwards packets locally to QGC while
independently decoding/counting the same incoming stream:

```sh
gst-launch-1.0 -v udpsrc port=15600 \
 caps='application/x-rtp,media=video,encoding-name=H265,clock-rate=90000,payload=96' \
 ! tee name=t \
 t. ! queue ! udpsink host=127.0.0.1 port=15601 sync=false async=false \
 t. ! queue ! rtpjitterbuffer latency=100 ! rtph265depay ! h265parse \
 ! avdec_h265 ! fpsdisplaysink video-sink=fakesink text-overlay=false sync=false
```

QGC live picture was visually verified. This uses Ethernet from the Pi to the PC. The
Pi hotspot is configured for band `a`, channel 36, but that configuration is not proof
of tested 5 GHz delivery. The PC's sole WiFi interface currently carries its Internet
connection; no disruptive network switch was made. WiFi and optical glass-to-glass
latency remain separate measurements. The receiver's 100 ms jitter buffer setting is
not total latency.

Native Chrome checks exercised person selection, the main depth inset, and the 3D page
with GCL2 geometry. The vehicle stayed grounded; no flight or PX4 commands were sent.
The 3D view is a single-camera-frame point cloud, not SLAM or an accumulated room map.
