# Chennai engineering handoff · OAK-D Pro, Pi 5 and calibration workspace

Use the operator field guide in this directory for printed targets and measured captures. This engineering handoff covers a Pi that already has a working Hailo detector and a CM3 profile. On a new installation start with `docs/DEMO_USER_GUIDE.md` in the checkout.

## 1. Get the public test release

Run the following on a workstation in a directory where you want a fresh checkout.

```sh
git clone --branch v2026.11.18-preview.1 --depth 1 https://github.com/bhanuprakashjh/garuda-pi5.git garuda-pi5-preview
cd garuda-pi5-preview
git rev-parse HEAD
```

Record this commit, the active revision on the Pi, camera serial and mount revision with each result. The public tag includes the printable targets, page source and calibration program. A team that received an earlier transfer kit can verify that installation using its matching kit checkout. The receiving tool checks the bytes of the installed handouts. Keep site addresses, calibration sessions and identifiable images in your team's evidence store.

## 2. Add OAK stereo to Chennai’s working CM3 station

The owner confirms the Chennai team already runs CM3 and Hailo successfully on another Pi. Preserve that as the known-good baseline. The CM3 may remain connected to CSI while OAK uses USB 3. This release selects the demo input at service startup and uses one active camera profile. The two profiles can share the detector code and Hailo models. OAK stereo depth is registered to OAK RGB. Combining that depth with a CM3 detection would require separate cross-camera timing and extrinsic calibration.

1. **Record CM3 first.** Save its source label and working detection readout. Record the deployed Git revision, active service names, model paths and checksums. On the receiving Pi inspect `systemctl cat garuda-mono` and `systemctl show garuda-mono -p ExecStart -p DropInPaths`. Preserve copies of the unit, effective drop-ins and referenced environment files locally. Use the site's working mono-depth settings when returning to CM3.
2. **Add the OAK-specific components.** Check USB 3 and normal-user device access, the separate DepthAI environment, host OpenCV/NumPy, the single OAK camera owner on Pi port 8096, and the calibration workspace on Pi port 8099. See `hosts/pi5/oak/README.md` for the device/USB prerequisites and `docs/OAK_HOST_STEREO.md` for host stereo. Keep the working CM3 detector environment intact.
3. **Stage an OAK service profile.** For the shipped box-depth demo, the detector settings are `GARUDA_MONO_CAM=oak`, `GARUDA_MONO_OAK_BOX=1`, `GARUDA_MONO_OAK_PAIR=0` and `GARUDA_MONO_SKIP_DEPTH=1`. Here “skip depth” disables the monocular model. Stereo comes from the separate host camera owner, configured with `--host-stereo --map-on-demand --map-interval-ms 500` on the sending station. Review local service paths before switching. The generic host-stereo enabling script alone does not select OAK as the detector input or reproduce all of those owner flags. See `docs/UNIFIED_CAMERA_PROFILES.md` for service-start profile selection.
4. **Verify OAK at the bench.** Open the demo, confirm fresh OAK RGB detections, then open the paired depth inspector and confirm advancing pairs. Run the receiving check below *with the OAK profile active*. Begin board/tape capture on this profile. Record CM3 and OAK results as separate camera configurations.
5. **Verify the return path.** In a bench window restore the saved CM3 detector settings and prior service states. Confirm CM3 RGB and fresh detections again. The OAK owner's capture-only option still uses the OAK and is a different operation from restoring CM3. Keep the two service profiles for future tests.

From the bench PC, substitute the receiving Pi’s reachable hostname:

```sh
python3 bench/oak_receiving_check.py --host PI_HOSTNAME --out chennai-receipt-01.json
```

The check takes two owner snapshots and reads the demo state and three handouts. It compares served bytes with the checkout, records the depth backend, camera compute mode, source hashes and runtime versions, and exits nonzero on a mismatch. Use a fresh output filename on retries. Keep the JSON with the revision and station ID. A pass covers the observed profile and endpoints; board geometry, Hailo throughput, IMU timing and distance accuracy have their own tests. During startup, wait for a fresh OAK RGB frame and repeat if the demo source is absent.

For diagnosis, run these read-only commands **on the Pi**:

```sh
systemctl is-active garuda-oak-preview garuda-mono garuda-calibration
systemctl show garuda-oak-preview garuda-mono garuda-calibration -p Id -p ExecStart
curl -fsS http://127.0.0.1:8096/health | python3 -m json.tool
vcgencmd measure_temp
vcgencmd get_throttled
```

The sending station uses OAK capture and optional on-camera H.265, `--host-stereo` with Pi SGBM, on-demand full maps and a 500 ms map interval. Hailo runs the detector. The previous owner's QGC video destination, local SDK paths and laptop address are site-specific. Review network destinations before reusing a sender's configuration on a new LAN. A stationary on-demand map counter is expected when all map viewers are closed. To examine display behaviour, open a viewer deliberately and record the extra workload. The sending run had IMU streaming disabled.

| Component | Normal entry point | Where to look in the checkout |
| --- | --- | --- |
| Camera owner and geometry | Pi 8096, one owner for the OAK | `hosts/pi5/oak/preview.py`, `host_capture.py`, `geometry.py` in that directory |
| Demo and range display | Pi 8098, `/demo`, `/demo/cloud` | `hosts/pi5/garuda_mono.py`, `hosts/pi5/demo/depth-view.js`, `cloud.js` |
| Calibration centre and camera workspace | Pi 8099, `/`, `/camera` | `hosts/pi5/oak_calibration.py`, `hosts/pi5/demo/calibration*.{html,js,css}` |
| Matched capture provenance | Owner's loopback `/pair-preview?depth=1` | `hosts/pi5/oak_pair.py`, `Workspace.capture` in the calibration service |
| Reports and timing | Workstation CLI | `bench/oak_tape.py`, `bench/oak_depth_views.py`, `bench/oak_soak.py`, `bench/oak_imu_analysis.py` |

On an already provisioned host, `bash hosts/pi5/oak/install-calibration.sh` installs the calibration files, enables/restarts its unit and can add its navigation link to an older demo. Review the target paths before invoking it. Preserve the installed calibration program, UI, unit and data first. To roll back, restore the prior program/UI/unit from that station's backup and restart only the calibration service. Keep the session directory. Agree a bench window with other operators before changing the active service and identify the host in the test record.

## 3. What the software records

Session data defaults to `~/.local/share/garuda/calibration/<session UUID>/` on the Pi. Setup is fixed per session. The service binds captures to mode, epoch, calibration ID and geometry. A duplicate image digest or sequence is rejected within a session. A restart requires a new session. The independent split is an operator decision; hashes cannot detect an operator quietly reusing the same physical setup.

The exported ZIP contains:

| Entry | Meaning and review |
| --- | --- |
| `session.json` | Config, board condition, tape reference, creation revision and code hash, OpenCV version, binding and capture list. Current captures also include timestamp, capture revision, measured confirmation and optional tape uncertainty. Inspect the individual provenance for pair and geometry fields. |
| `<id>.png`, `<id>-depth.png` | Exact-pair delivered RGB and uint16 millimetre depth when present. Zero means missing. Preserve native resolution and type. |
| `<id>-preview.jpg`, `<id>-depth-preview.jpg` | Operator inspection images. Colour and scaling are for display. |
| `board-tape.csv` | One line per saved capture, split, sequence, tape and board values, valid-pixel fraction, raw signed difference and comparison status. Failed HTTP attempts before a capture was saved belong in the operator log. |
| `board-tape-summary.json` | Setup and validation have separate eligible/total counts, exclusion counts, median signed difference, median absolute difference and maximum absolute difference. Empty sets carry null statistics. Aggregates weight each eligible capture equally; inspect the per-distance CSV first. |
| `candidate.json` when solved | Candidate intrinsics for the delivered RGB raster and held-out reprojection residuals. Check which capture IDs entered the solve. |

The board median comes from nonzero pixels inside an eroded hull of detected points. A raw signed difference uses the operator's tape datum. It can include tape zero, reference geometry, alignment and depth bias. It is insufficient to identify a camera-origin offset. A new measured-depth session requires explicit tape confirmation on each placement. Old records with missing provenance can still be exported. Preserve them as historical observations. Keep the guessed 0.4 m entry out of comparison. No process in this handoff automatically applies a candidate or correction to the sensor.

## 4. First investigations after the team's captures

1. **Board and reference.** Read the marked endpoints and uncertainty. Confirm sheet dimensions and flatness in the photos. Overlays should align with the actual printed points. For each distance report number attempted, saved, compared and missing, depth coverage, signed difference and spread on repeats. Put failures beside successful readings. If the board is tilted for the lens solve, keep those observations separate from face-on tape checks.
2. **Edges and alignment.** Use paired RGB/depth views at centre and edges. Inspect physical target edges for a consistent spatial shift versus holes or occlusion. Record the exact stream geometry and the calibration ID for each observation. A visually plausible point cloud alone does not verify the distance axis.
3. **Selected-person readout.** Compare the demo only with independent stationary, consenting bench observations that define the sampled torso plane. Inspect `python3 bench/oak_tape.py collect --help` before collecting. The tool needs the actual deployed producer and consumer revisions and a separate label per recording. Use its `tune` and `validate` sets, then `python3 bench/oak_tape.py score RECORDING_DIR --set validate`. Preserve unavailable results and configuration changes. Leave geometry verification unset until a reviewer has evidence of the axis, datum, offset and uncertainty.
4. **Mount repeatability.** Use the operator's disarmed mounted-camera session. Compare coverage, visible obstructions, USB/power and lighting with the bench baseline. If the mount changes, label a new configuration and repeat independent checks. A bench or grounded mounted-camera result alone gives no airborne acceptance.
5. **VIO and mapping backlog.** First deliver paired left/right images, raw accelerometer and gyro records, timestamps, coordinate conventions, camera serial and a restart/discontinuity manifest in a replayable recording. Verify monotonicity, inter-frame gaps and image/IMU time alignment. Then evaluate camera–IMU extrinsics and a handheld closed walk against an independent reference. Keep odometry evaluation and map loop-closure evaluation separate. Current host-stereo GUI depth checks do not supply this dataset.

For the previous fan-cooled run, all five views completed twice for 30 seconds each. The Pi cooled from 71.4 to 56.5 °C with zero active throttle samples out of 300. Detector throughput was 13.4–14.9 frames/s. Fine 3D displayed about 1.9 depth pairs/s and Full about 1.0. These are a comparison baseline for the sending station, with a desk fan. Recheck on the receiving Pi and after mounting before making local performance claims. The public release record in `docs/RELEASE_2026-11-18.md` describes the scope of those measurements.

## 5. Change and review workflow

Use an isolated calibration Python environment with NumPy, OpenCV and pytest, plus Node for the UI tests. The calibration service uses the host's existing OpenCV and NumPy. The printable PDF is a static asset. No PDF renderer is needed on the Pi. To change the sheets, regenerate them with workstation Pycairo using `python3 calib/targets/build_oak_handout.py`, render each page and run it through `oak_calibration.detect` with the preset dimensions. Recheck both physical scales on the printer.

```sh
PYTHONPATH=hosts/pi5:hosts/pi5/oak:bench python3 -m pytest -q hosts/pi5/test_oak_calibration.py bench/test_oak_receiving_check.py
node --test hosts/pi5/demo/calibration-guide.test.js
node --test hosts/pi5/demo/*.test.js
```

To reproduce a failure, copy its ZIP into a private evidence directory, retain its digest, record Git SHA and source hashes, and reduce the case to a synthetic fixture where possible. Exercise capture or export through the same route the operator used. Run the full Pi-side suite in the project's prepared environment before proposing a release. Preserve compatibility with old sessions, keep candidate application explicit, and document changes in `bench/benchlog.md`. Do not commit raw participant images or per-site secrets.

Return a review with the change's commit range, exact tests and counts, hardware and active profile, what was observed on hardware versus replay, a failing example, remaining unknowns and the owner of the next measurement. An appropriate first Chennai issue is “repeat the board comparison on this mount and explain the largest repeatable residual.” Avoid tuning against the validation set to make that issue disappear.

## 6. Return two independent records

**Mounted camera.** Follow OPERATOR.md for the printed board, measured tape distances and held-out placements. Include the camera serial, mount revision, capture IDs, source commit, geometry ID and the number of missing readings. Preserve the raw measurements and the report for that rig. The GUI lens candidate remains unapplied.

**Physical FC.** The owner reports a Pi-to-FC Ethernet test that reached Offboard and spun the motors with the props removed. If the earlier log exists, attach the original FC log, exact firmware, sender revision and test conditions. A fresh camera calibration cannot establish the FC's response to a disconnect or pilot takeover.

For the receiving bench, keep the propellers removed. This bridge release accepts physical endpoints in receive-only mode. Record FC firmware and configuration, the Pi release and both clocks. Compare FC-side mode and arming records with the monitoring display. Record the before, interruption and reconnect windows. Mark intervals with missing data as unknown. Confirm that the observer emitted zero MAVLink packets. Check that rebooting a display or opening a second tab confers no authority. Give the raw logs and your pass/fail criteria to a second reviewer.

Separate sign-off is still needed for the configured aircraft's takeover and failsafe behavior. The receive-only exercise gives evidence about observation and link recovery. It cannot demonstrate motor response, airborne position hold or physical orbit.
