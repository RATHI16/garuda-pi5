# OAK calibration workspace

Open **Calibration** in the demo header or `http://<Pi-IP>:8099/`. This is a separate
bench service, so neither Hailo inference nor the camera owner is replaced. It reads the
owner's loopback endpoints. No new Pi packages, camera flash writes or factory calibration
changes. Existing OpenCV 4.10.0 and NumPy are used. OpenCV is Apache-2.0; no new dependency.

## Team workflow

1. Mount the target on a rigid flat backing and check for bowing. Record board identity and
   condition honestly. Bent/unknown boards can be archived as exploratory captures but
   cannot produce an intrinsics candidate. Selecting “flat” is an operator declaration,
   not an automatic flatness measurement.
2. Create a session with operator/camera labels, pattern and **measured** pitch. For a
   checkerboard count inner corners (9×6 corners means 10×7 squares); pitch is square side.
   Symmetric circles use centre spacing. Asymmetric circles use coordinates
   `(2*column + row%2, row)*pitch`; pitch is row spacing, same-row centres are twice that.
   Other printed patterns can be added later with a detector and object-point definition;
   arbitrary patterns and ChArUco are not implemented in this version.
3. Capture at multiple distances and tilts, covering the frame centre and corners. Keep
   the whole pattern visible and still. At least 12 detected tuning views and 3 distinct
   validation views are required. This count is only an implementation minimum, not a
   guarantee of good conditioning or calibration quality. Nearly identical poses are
   rejected by the solve. Record tape distance and its precise reference when available.
4. Each capture saves the original RGB PNG, detected points/overlay and metadata. In stereo
   mode it also saves the exact pair's uint16 depth PNG, timestamps and delivered geometry.
   Raw RGB mode has no depth. Mode/epoch/geometry changes require a new session; no latest
   depth is silently attached to another RGB frame. Hash and sequence checks reject duplicates.
5. Estimate a candidate. The result models the **delivered RGB stream**: raw RGB in normal
   mode, already-undistorted RGB in stereo mode. It is NOT a new stereo/factory calibration.
   Held-out reprojection fits the pose of each validation view and reports pixel error.
   It does not validate depth scale, the depth axis/origin, IMU extrinsics, or flight behavior.
6. Export ZIP for review/archival. The candidate identifies exactly which captures it used;
   later captures are not automatically included until the solve is rerun. Nothing is applied
   automatically. Record a new session if changing pattern, pitch or board condition.

Depth median, when available, uses an eroded convex hull inside the detected target. It is
labelled unvalidated and is not the person estimator or a tape accuracy score. Tilted boards
span multiple depths; their median cannot be compared blindly to a tape reading at one point.
Use the existing `bench/oak_tape.py` workflow for separate person-distance validation.

## Installation and data

```sh
# Run as garuda, from a checkout/staging tree containing hosts/pi5:
bash hosts/pi5/oak/install-calibration.sh
```

This copies the service to `/home/garuda/garuda-calibration`, installs/enables
`garuda-calibration.service`, and patches ONLY a navigation link into the existing demo.
Original demo assets are backed up in `~/.local/share/garuda/calibration-install-backup/`.
The calibration installer does not restart mono/OAK. The separate stereo-enablement action below changes the owner configuration. A page reload is needed.

Sessions persist in `~/.local/share/garuda/calibration/<UUID>/`, with source SHA256 in each
manifest. Limit: 20 sessions, 60 captures/session, 1 GiB workspace storage. Export before
archiving complete session directories; no destructive delete button is provided. No images
are stored in Git. Capture/solve/export are serialized, OpenCV uses one thread, and the UI
requires deliberate individual captures. Calibration processing adds CPU/disk load; do not
mix it with benchmark runs without recording that workload.

This inherits the trusted bench LAN: it is not an authenticated team account service. All
LAN viewers can see sessions and images; write requests require same-origin JSON. Keep the
workspace on the bench network. Service stop: `sudo systemctl disable --now garuda-calibration`.
Stopping leaves datasets and navigation link intact for later restart.


## Guided depth mode (enabled 2026-11-12 at owner request)

The standing owner now runs `--stereo`; IR stays off. Explicit enable/rollback:

```sh
bash hosts/pi5/oak/enable-stereo.sh
bash hosts/pi5/oak/enable-stereo.sh --disable
```

This installs the tested owner modules under `/home/garuda/garuda-oak-owner`, creates
`/etc/systemd/system/garuda-oak-preview.service.d/20-garuda-stereo.conf`, restarts OAK and
mono, and waits for stereo health. Original owner source remains intact as rollback.
Default mono still consumes RGB only; its image is now undistorted because stereo is on.
No factory calibration, inference model, IR or flight-control change is made.

The calibration page now reads a matched pair for BOTH live preview panels, at roughly
one request every 1.2 seconds. Depth uses a fixed 0–6 m display scale; black is invalid.
Valid-pixel share describes image coverage, not accuracy/confidence. A stale preview clears
and capture disables. Captures fetch their own fresh pair; they do not reuse the displayed
JPEG or borrow depth from another frame. New sessions require matching depth; missing depth
rejects the capture. Old RGB-only sessions remain readable and exportable.

Choose a task:
- **Depth at measured distances:** three setup observations, then three separately captured
  checks. Keep the board facing the camera, move through near/middle/far positions while
  the whole pattern stays visible, state the tape reference and enter each measured distance.
  Pattern detection, a saved depth array and a valid board median are needed to advance the
  guide. Counts are guidance only; no numerical distance-accuracy gate is asserted.
- **RGB lens model:** twelve varied setup views and three held-out views. Vary position,
  distance and tilt. With stereo on, this models the already-undistorted delivered RGB,
  not the raw lens. Candidate generation is still advanced and never applied automatically.
- **Practice:** learn pattern framing and inspect RGB/depth without a measured distance.
  Bent/unknown targets remain exploratory and cannot produce a lens candidate.

Pattern cards and a configurable diagram illustrate checkerboard inner corners, symmetric
circle centres and offset-circle spacing. Session labels are folded away. The next-step
card and four-item checklist stay beside the capture controls. Distance is cleared after
each capture so an old tape reading is not silently reused. Review shows detected-corner
RGB and saved depth together, with board depth explicitly labelled unvalidated.

If the owner restarts or its mode changes, an existing session is blocked from appending
frames. **New depth session with these details** copies the board/operator setup into a
new editable form, without copying recordings or changing the old session. Check flatness,
pitch and tape reference again before starting. No data migration/deletion is required.

## Calibration centre (2026-11-12)

The main demo's Calibration link now opens the centre at :8099/. The existing
camera/depth workspace is at /camera, with unchanged session storage and exports.
The centre adds guided **manual** checks for detection, gestures, monocular distance,
and tracking. Operators download JSON observations before closing/reloading the tab;
these new observations are not persisted on the Pi or automatically scored. Measured
mono references require tape confirmation and a named reference; guesses are practice
only. Existing 0.4 m board records were explicitly described by the owner as guesses;
they remain preserved and must not become ground truth.

Automatic model/gesture tuning and mono fitting/apply/rollback remain future work.
No existing model, thresholds, factory calibration or control behavior is modified.
