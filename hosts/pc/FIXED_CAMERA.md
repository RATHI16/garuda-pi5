# Fixed real camera → Gazebo diagnostic

Run `python3 hosts/pc/fixed_camera_bridge.py --seconds 600` from this worktree and open http://127.0.0.1:8103. Requires running grounded SITL on 8099, the Pi camera/demo on 8098, Gazebo GUI in partition `garuda_isolated`, and Gazebo Python bindings. Coordinate Pi use through the shared lock before starting. The bridge stops after ten minutes by default.

This is a read-only diagnostic, with no flight commands. The browser displays the real camera stream. Choose a person in the linked camera controls, then explicitly confirm that selection locally. A green sphere in namespace `garuda_fixed_camera` represents a qualified torso-region measurement. Existing scripted Gazebo actors are unrelated to the real camera. Loss, stale data or restart requires explicit reselection. Unqualified/preview depth and box-size distance never produce a measured marker. Arming or stale simulator telemetry suppresses it.

The current Pi baseline supplies RGB and Hailo detection, with measured stereo disabled. Therefore a live measured marker is not yet available. The additive producer provenance changes in this commit are not deployed. No camera or detector settings were changed for this diagnostic.

For future qualified operation, `--calibration profile.json` loads: `position_m` (three world coordinates), `world_from_optical` (proper orthonormal 3×3 rotation), `calibration_id`, `mount_verified: true`, and `evidence_ref`. These flags are assertions backed by physical verification, not a calibration procedure. Do not set them based on guesses. The source must separately report qualified non-preview depth, verified undistorted RGB optical geometry and an exact matching capture pair. Geometry identity must match the mount profile.

A level camera aimed along Gazebo +X uses rotation [[0,0,1],[-1,0,0],[0,-1,0]]; translation and actual orientation require measurement. The camera transform is fixed and never uses the simulated drone's pose. ROI-centre projection is a torso-region approximation, not person centre ground truth. Validate against independent measured positions before enabling any subsequent simulated following.

Live check on 2026-11-13: source frames arrived, simulator reported GROUND/disarmed/landed with fresh telemetry, and unselected/unqualified input produced no position. Gazebo marker-list confirms the owned marker absent. Positive projection is covered only by synthetic tests; no real-camera distance validation or flight was performed.

## Explicit stereo preview

The diagnostic page now fetches paired RGB and depth from the Pi calibration service's `/api/live` endpoint. Only `pi-sgbm` depth is displayed; other backends are suppressed. It does not display the original monocular-depth inset. Preview pixels remain separate from the qualified-position path: enabling the view does not enable a measured Gazebo marker or following.

For a bounded host-stereo session use the staged `run-cpu-depth-session.sh 600` under the coordination lock. On 2026-11-13 this was enabled at the owner's request: live output reported `pi-sgbm`, depth present, qualification false and approximately 15% nonzero pixels. Temperature was 61.2 C with no current throttle flags at the check. This is a preview observation, not an accuracy or endurance result. Capture-only mode restores automatically on expiry or thermal stop.

Selection now stays on the diagnostic page: the detected-person buttons call the existing Pi coordinate-selection endpoint and confirm the returned state has the intended ID/epoch before selecting locally. RGB and stereo are displayed side by side. This endpoint changes the detection lock only, never flight state. Live selection returned HTTP 200 and the Pi lock changed to the requested visible person; the stereo preview continued. The old link to the monocular demo was removed.

## Independent detection and stereo display

The current UI supersedes the slow paired-RGB calibration view above. The left panel uses the existing Hailo detector's clean MJPEG stream, with its latest track boxes overlaid and clickable; all visible object classes can be selected. Selection changes only the existing detector lock. Low-confidence or non-person selections never qualify a torso measurement. The right panel independently reads completed host stereo pairs through an SSH loopback tunnel and displays depth only, with no calibration-service one-second cache. Latest detection overlays and stereo images are not frame-associated and are never fused as a measurement.

Run using the existing PC vision environment (OpenCV, NumPy and Gazebo bindings):

```
.worktrees/px4-gazebo-demo/.runtime/vision-env/bin/python .worktrees/oak-owner-service/hosts/pc/fixed_camera_bridge.py --seconds 600 --depth-host garuda@10.42.0.128
```

The bridge owns its SSH tunnel and closes it on normal exit/SIGTERM. Stereo preview cache expires after one second without a distinct pair. Reported preview FPS counts distinct delivered pairs, not browser redraws and not total producer throughput. During a short live check the preview delivered 4.3–4.5 frames/s and detector-reported processing rate was 7–8 fps; these are observations under that load, not an endurance benchmark. The camera owner initially hung on its configured SIGTERM stop; a targeted SIGINT completed graceful shutdown and the bounded stereo session started. No systemd defaults were changed.

The longer live check exposed an older producer issue: RGB timestamps drifted roughly 23 ms from the left/right pair while the two mono timestamps differed by about 19 microseconds. Requiring the entire triple within 5 ms starved the depth worker despite usable stereo mates. The explicitly unqualified host-preview path now retains the 5 ms left/right check but allows the entire RGB/left/right span up to the already-published 33.333333 ms pair limit. Default TripleSelector behavior is unchanged for other callers. Both limits are exposed in health. This is an explicit relaxation of **preview RGB association**, not improved sensor synchronization or qualified alignment. No calibrated object depth or following is enabled by it.

After that producer change, the 70-second sampled check kept fresh depth at every sample: depth processed count advanced from 76 to 436, latest-depth age at samples was 36–138 ms, and displayed unique preview rate settled near 6 Hz. Detector-reported FPS was 8.4–9.7. Only two startup RGB-span rejections were counted and that count did not grow during this check. Temperature spot check was 67.75 C, no active throttle bits. These sampled observations are not proof of uninterrupted coverage or endurance. Evidence: `bench/results/2026-11-13-independent-stereo/live-check.json`. Live person selection returned HTTP 200 while stereo kept updating. The revised producer is in the transient `/home/garuda/independent-depth-stage` session only; persistent capture-only configuration remains unchanged.

## Selected-person scalar preview and detector-only mode

`stereo_person_preview.py` reports **the median nonzero stereo pixels in the central torso region**, plus pixel count, coverage, p10/p90 and age. It is explicitly unvalidated, not the qualified `oak_distance` estimator. Its 30-pixel minimum is a diagnostic floor, not an experimentally validated confidence threshold. Multiple surfaces and occluders can bias the median. No value is used for flight or the qualified Gazebo marker.

The detector snapshot now includes `frame.capture_host_ns`, reconstructed from its existing capture timestamp `t`. The PC keeps a bounded detection history and requires the same camera epoch and detector-process identity, matching RGB capture timestamps within 1 microsecond (float-to-integer reconstruction tolerance), matching image dimensions, and a visible selected person in that matching detection. There is no latest-frame box fallback. The underlying host stereo preview still allows RGB/mono span up to 33.333333 ms: matching the RGB frame is not physical exposure synchronization. The readout expires at 700 ms source age and is cleared on selection/process/epoch changes.

`GARUDA_MONO_SKIP_DEPTH=1` skips both loading and inference of the two monocular depth engines. The existing detector/model/thresholds remain in use. This is opt-in and defaults off. A transient `garuda-stereo-distance-detector.service` runs an isolated copy of the deployed detector with that flag and the additive timestamp field. It conflicts with the original `garuda-mono.service` and starts the original service on exit. The deployed original source files were hash-checked unchanged; the stage is `/home/garuda/stereo-distance-detector`, and the exact narrow patches are recorded in `bench/results/2026-11-13-stereo-person-preview/`.

A 12-sample live check contained a stereo number in 10 samples, ranging 0.782–0.847 m as the scene/person moved; the other two were unavailable. Earlier spot values were 0.92–0.93 m. None was tape-measured. Detector-reported processing was about 12 fps with host stereo active (about 15 with stereo temporarily absent), and delivered stereo preview varied around 4–6 fps. These are observations, not a controlled before/after benchmark. The UI now shows the number beneath the views; selecting a suitcase intentionally suppresses the person/torso readout.

Still outstanding: direct sensor sequence provenance instead of reconstructed timestamp matching; detector result history/long-polling to avoid snapshot misses; atomic ID-based selection instead of the legacy coordinate endpoint; lower stereo/transport latency; meaningful depth reliability/ambiguity checks; physical validation and a fixed-camera world pose; integration into the main GUI. The current readout must not be advertised as accurate person range.

## Delivery diagnostics and transfer behaviour (review branch, offline only)

Changes after review claude-0058. Not yet run against the Pi; no rate, latency or CPU
effect has been measured (all `TBD-bench`).

- The depth reader passes the pair cursor (`after`, `epoch`) to `/pair-preview`. The
  owner long-polls and answers 204 when there is no newer pair, so each pair is
  transferred once. The reader keeps the latest pair in memory and retries the person
  association against it when a later detector result arrives, for at most 700 ms.
- Gazebo's marker service is contacted only to show, refresh (every 0.25 s) or remove a
  marker. With nothing shown and nothing to show the detector poll loop makes no request.
- `/api/state` carries `delivery`: polls, poll errors, measured poll rate, distinct
  detector results seen, and results the detector produced over the same span (from its
  inference counter). The difference is results the latest-state poll skipped.
- `/api/preview` carries `counts` (pairs, pairs with a reading, pairs without one keyed
  by the final reason, empty long-polls, read errors) and `reader_error` with the cause,
  including a failed start under an interpreter without OpenCV.
- POSTs are accepted only when Host is this loopback port and Origin matches it.

These counters describe delivery to this page. They are not availability, accuracy or
acceptance figures, and the scalar remains an unvalidated torso-pixel median.
