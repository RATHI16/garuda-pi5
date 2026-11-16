# OAK/Pi/Hailo to PX4: simulation-first execution plan

Owner directive, 2026-11-12: move beyond camera bench work into Gazebo/PX4 control.
This expands the earlier bring-up stop boundary to simulation integration. Real
flight, real-FC parameter changes and motor commands are not authorized by this plan.

## Current result — 2026-11-13

Stationary Hailo-in-the-loop convergence, dropout Hold, explicit reselection/Follow
and landing passed with ground-truth scoring. 174 producer results reached and were
evaluated by control over a 14.90-second follow interval. A dropout while commanded
at 1 m/s produced a sampled latched Hold within 0.203 s and 0.597 m sampled travel. See
[run evidence](../bench/results/2026-11-13-hailo-moving-dropout/README.md).
Restoration now verified on hardware after a telemetry-only upgrade to the legacy
Pi demo. Model/threshold/capture behavior preserved. Normal PC demo restored and
simulator grounded/disarmed. This closes the immediate deterministic stationary
check, not the full plan: shared host/Hailo stereo, moving/two-person/obstacle faults,
physical calibration and VIO remain separate gates.

## What already exists

- OAK RGB capture -> Hailo detector/tracker -> Pi :8098, with capture-only standing
  mode. Host stereo is explicit, bounded and unqualified; it cannot supply trusted
  metric following or obstacle clearance yet.
- PC lab: hosts/pc, services/garuda-follow/sitl_demo.py and sitl_demo_core.py;
  isolated ROS domain 42, namespace /garuda_sitl, XRCE port 8889, PX4 instance 3.
- Desk-camera mode maps image position/box size to a schematic world target.
  It demonstrates command plumbing, NOT onboard target localization.
- Gazebo mode uses a drone-mounted RGBD camera, PC detector and simulated depth.
  This closes the camera/vehicle feedback loop, but is NOT measured Hailo throughput
  and is NOT yet the same stereo algorithm as the Pi.
- Existing smoke harnesses cover takeoff/land and visual follow/dropout/orbit.
  Historical results must be rerun against the current session before acceptance.

## Execution order and exit evidence

1. **Recover baseline SITL now.** Start pinned existing PX4/Gazebo build without
   upgrades, refresh DDS/controller state, verify fresh disarmed telemetry. Run
   takeoff/hold/land and preserve logs. Then exercise Gazebo onboard-camera follow,
   dropout hold and landing. Leave the software aircraft grounded. Existing Pi
   services/files are not replaced by this phase.
2. **Observation/control contract.** Add an adapter with explicit source identity,
   camera epoch/frame sequence, observation age, selected-person ID, coasting flag,
   camera intrinsics/extrinsics and depth qualification. Host timestamps cannot be
   subtracted across Pi/PC clocks without measured synchronization; carry local
   receipt ages plus source-reported age. Reject nonfinite data, wrong target/frame,
   repeated/stale frames, restart epochs and unqualified metric depth. Explicit
   reselection after restart; no invisible fallback from depth to box-size distance.
   Test malformed input, disconnect, identity ambiguity and missing depth.
3. **Same stereo path in sim and on Pi.** Extend the isolated Gazebo model with
   stereo cameras matching recorded OAK geometry, expose left/right and IMU through
   the agreed sensor contract, and run the same host stereo plugin. Keep existing
   simulated RGBD as a labelled reference only. Use sim time for sensor stamps and
   monotonic wall time for transport watchdogs; pause/reset tests must hold commands.
   Feed images through Hailo only via an explicit isolated input adapter; PC detector
   results remain labelled PC. Compare against Gazebo ground truth offline; ground
   truth must never enter the perception/controller input.
4. **Qualified distance + controller.** Resolve occlusion/sparsity and physical board
   geometry; tune on one recording set, validate on held-out measured recordings.
   Quantify bias/error/valid-output fraction, update interval and capture-to-command
   latency. Freeze estimator/controller versions and limits before acceptance runs.
   Horizontal standoff is a transformed geometry quantity, not blindly optical Z.
   Initially fixed altitude and bounded yaw/horizontal velocity in obstacle-free sim.
5. **Fault and obstacle scenarios.** Person crosses image, stops, disappears, two
   people cross, camera/NPU restarts, network delay/drop/reorder, estimator rejects
   depth and CPU thermal limiting. Verify time-to-hold, command limits, target identity,
   minimum separation and recovery/reselection. Unknown depth is unknown space.
   Add obstacle avoidance and trajectory constraints before cluttered follow;
   person following alone is not collision avoidance. Log offboard-link-loss behavior.
6. **Localization separately.** Prove the basic loop with simulator/GPS pose first.
   Then camera–IMU calibration, handheld VIO and GPS-off EKF2 simulation with measured
   latency/covariance/frame conversion. Only continuous VIO goes to EKF2; loop closure
   stays outside that estimator input. Evaluate drift and resets against sim truth.
   Revisit ROS/PTP deployment only when this stage requires it; no casual Pi installs.
7. **Full load and operational scenarios.** Stereo + IMU + Hailo detection + OAK
   H.265 + WiFi, with per-stage timings, CPU, thermals and failures. Then mapped indoor
   return-to-home and cluttered/forest follow in simulation. These need localization,
   planning, free-space evidence and a recovery policy; a camera/depth map alone
   cannot provide them. Establish measured acceptance limits before calling any run
   a pass. Real-hardware flight has a separate later review and authorization.

## Immediate deliverables and ownership

Codex owns integration, tests and evidence; existing other worktrees remain intact.
Use the shared Pi lock for any Pi activity. Camera calibration/tape ground truth and
physical cooling need an operator; they do not block baseline Gazebo work.
Every milestone gets commands, exact code/build identity, measured results, assumptions
and blockers in benchlog.md. Keep a reusable one-command smoke path and visible
readiness state (camera, detector, depth qualification, telemetry, simulator, control).

Do not report one global percentage complete: command plumbing, physical perception,
localization and autonomy have different evidence gates. Current depth remains preview
only; simulator success does not validate physical distances or real-flight safety.

## Test ladder: keep the claims separate

| Run | Sensor input | Vehicle | What it can establish |
|---|---|---|---|
| Baseline flight | None | PX4/Gazebo | Arming, takeoff, hold, land, DDS/telemetry |
| Desk-camera demo | OAK RGB + Hailo person boxes | PX4/Gazebo | Real perception -> simulated commands, with schematic target mapping |
| Onboard visual baseline | Gazebo RGBD + PC detector | PX4/Gazebo | Camera/vehicle closed-loop behavior and dropout hold |
| Shared stereo simulation | Gazebo stereo -> same host/Hailo plugin | PX4/Gazebo | Algorithm/controller compatibility with scored synthetic geometry |
| Recorded physical replay | OAK stereo + calibration + labelled tape | No live commands | Real sensor quality, latency and rejection behavior |
| Full hardware-in-loop | Explicit simulated video ingress to Pi/Hailo | PX4/Gazebo | Target deployment load and feedback loop, once ingress is implemented |
| GPS-off localization | Stereo/IMU VIO | PX4/Gazebo | EKF2 fusion, drift, frame/time conventions and reset response |

A successful row does not substitute for the rows below it. Tests must record source
kind and code identity so the existing schematic demo cannot be mistaken for metric
OAK autonomy. Hailo stereo remains exclusive experimental mode until shared-NPU
scheduling and model licence/quality decisions are resolved.

## First execution result — 2026-11-12

Baseline SITL recovered; takeoff/land passed. New-world camera timestamp retention
was found and startup recovery corrected. Gazebo RGBD/PC-detector follow + dropout
hold + landing smoke passed; orbit was sampled, not scored for full completion.
Simulator left grounded/disarmed with fresh telemetry. See benchlog and
bench/results/2026-11-12-sim-restart. Next: stage 2 observation contract, then stage 3
shared stereo path. Physical OAK depth qualification is still a separate open gate.

## Gazebo -> Pi Hailo detection trial (2026-11-13)

First hardware-in-loop increment: `hosts/pi5/sim_detector_server.py` loads the same
YOLO26m HEF and HailoYolo26 wrapper as the working detector, confidence floor .15.
`hosts/pc/remote_hailo.py` sends raw BGR888 over an explicit localhost SSH tunnel;
request UUID and pixel SHA bind each response to exactly the RGB used for detection.
No cross-host monotonic timestamp subtraction. PC measures round-trip and ingress
age; Pi reports inference duration separately. Round trips over .5 s are rejected;
service/model identity changes reject results rather than silently reusing tracks.

Run Pi `GARUDA_SIM=1 bash run-sim-detector.sh` from a staged copy containing the
server, wrapper, thermal_cache.py, sim_restoration.py and oak/depth_session.py.
It has a 600-second systemd limit, exclusive NPU conflicts, bounded thermal checks
and ExecStopPost restoration of garuda-mono only if it was previously active. OAK
capture stays running; the original Pi detector is paused for exclusive NPU access.
SSH forwards PC 127.0.0.1:18102 to Pi 127.0.0.1:8102. Start PC vision.py with
`--hailo-rpc http://127.0.0.1:18102`, using the existing Gazebo Python environment.
Both entrypoints require GARUDA_SIM=1; PC vision also needs the isolated GZ partition.
After landing, stop the trial vision and Pi unit, close the tunnel and restart the
original PC vision. Hold the shared Pi lock for the entire trial/restoration.

Scope: **Hailo detection only**. Tracking remains the existing PC tracker; depth is
paired Gazebo RGBD, not host/Hailo stereo. No physical OAK image enters this loop.
No monocular-depth model, gesture pipeline or shared stereo NPU scheduler is loaded.
The existing YOLO26 AGPL benchmark-only restriction remains; no new model/dependency
or product licence clearance. `bench/hailo_sitl_smoke.py` explicitly checks the
remote engine before takeoff and exercises follow/dropout/landing. Failures must be
retained; a connection that delivers detections is not itself a successful follow.

Hardware detector increment tested: the first scene produced no follow movement;
a fresh-scene retry passed moving follow, dropout hold and landing using Pi Hailo
YOLO26. Both recordings retained. Original services restored after the bounded trial;
this is not the standing detector selection. Next incremental work is an operator
backend switch with lifecycle/reselection, shared tracking, then simulated stereo
through the host depth plugin. Physical depth qualification remains open.

### Review control gates implemented offline

3258415: A3 invalidates observation identity on errors, pauses and >0.5s processing
gaps; controller staleness also latches reselection. A4 uses PC-ingress metadata and
bounded pose receipt history, with a 50ms nearest-match limit and no current-pose
fallback for Gazebo projection. Current pose is still used for feedback control.
This is a receipt-time approximation; exposure synchronization is not established.
98 offline tests pass. Not deployed; scenario/scoring, delivery/thermal/restoration
fixes and airborne failure injection still precede stereo/tracker migration.

A5 offline implementation (8999a4d): simulation observations now use bounded new-state
long polling, with invalidations delivered as new revisions.20Hz control and existing
age gates remain. Local frame age excludes time waiting for a future publication;
legacy Pi-camera reads retain their old polling contract.111 tests passed, including
loopback HTTP behavior. Not deployed; no flight/latency acceptance result yet.

### Offline thermal, restoration and delivery instrumentation (2026-11-13)

Thermal sampling is cached for one second, with a 1.5-second maximum sample age;
failed or slow reads fail closed. Each vcgencmd call has a one-second timeout.
No on-device overhead or latency improvement has been measured.

The trial runner writes prior unit state, environment, model/wrapper hashes and
confidence under /home/garuda/sim-rpc-evidence/. It prints a post-trial verification
command. Run that command while holding the Pi lock after the trial stops: it opens
a viewer only for a previously active detector and requires fresh advancing frame
identities with a stable PID/restart count. An originally stopped detector stays
stopped. This is verification tooling, not evidence that restoration has passed.
Stage the new helper modules with the server before the next trial.

Controller state now carries delivery counts for distinct reader, controller and
evaluator frame identities, plus queue overwrites and the latest producer counter.
Compare interval deltas and epoch/generation identities; counts are cumulative for
the controller process. Evaluated means passed to the velocity evaluator, including
hold decisions; it does not mean a valid target or a nonzero command. Queue overwrite
counts include state transitions, not just detector frames. No live delivery fraction
has been measured. Deterministic scenario/scoring and explicit recovery reselection
remain prerequisites for the next simulation flight.

Review0049 tightening: restoration requires the trial unit to have ended in every
sample, plus advancing detector inference counters within one process instance for
a previously active detector. Viewer connection/errors are saved. Evidence files
are exclusive: use a new --out path on each retry; preserve failed attempts. Trial
health includes the imported wrapper hash for comparison with the prior detector.
EnvironmentFiles are listed but not resolved; do not infer effective configuration
from Environment/default values alone. These changes are offline-tested only.

### Deterministic stationary check (offline implementation, 2026-11-13)

Launch a **fresh owned** simulator with start_sitl.py --visual --stationary-check
(and --headless if appropriate). This mode refuses --attach and replaces only the
actor script with fixed (6,0,1) waypoints at 0 and 600 seconds. Normal demo world is
unchanged. Run bench/hailo_sitl_smoke.py with GARUDA_SIM=1 and
GZ_PARTITION=garuda_isolated in the existing Gazebo Python environment after staging
the reviewed detector/controller. This is stationary convergence/fault recovery, not
moving-target qualification. No run has yet exercised this mode.

Preflight checks generated world hash, fresh truth, fixed vehicle XY/yaw and actor
XY, disarmed/landed telemetry, Hailo identity, and script time under 120 seconds.
Target selection uses only detections: one eligible person, confidence >=0.5, fresh
range, no coasting, 5% image-edge margin, five distinct frames with one epoch/ID.
Truth subscriber stays in bench/sitl_truth.py, never the perception/control path.
It requires both named actor and vehicle poses in /world/garuda_lab/pose/info; if
Gazebo does not publish these together/freshly, it fails closed. The topic and
script-pose interpretation still need runtime verification before interpreting any
score as physical separation. No inferred actor path substitutes for missing truth.

Fixed limits are saved in every summary: 15 seconds follow, >=50 samples spanning
14 seconds, >=90% LOCKED, >=5 moving samples before dropout, no command above
1.01 m/s, >=2 m ground-truth horizontal separation, last five follow samples within
0.75 m of the requested 3 m separation. Dropout requires >=5 zero-command samples
after one second, with reselection latched. Recovery without operator action must
hold, followed by explicit selection (verified Hold) then a separate Follow.
Detector instance/model and selected identity must remain consistent within stages.
These are prospective test criteria, not measured performance or accuracy claims.

Cleanup attempts unpause and landing independently; summary records errors even if
landing or scoring fails. Missing samples cannot pass. A failed truth query can
prevent landing verification but does not suppress the landing command. Original
service restoration remains the separate reviewed checker. Evaluator counter now
uses the generation received with its snapshot. Shell restoration branch coverage
and unmatched-track wall-time bound remain open; the latter gates stereo.

### Runtime corrections to the stationary harness

The standard Gazebo pose topic omitted actors. Build `bash bench/sim_truth/build.sh`
before --stationary-check; the launcher adds the read-only BenchTruth plugin to a
copy of the existing server configuration. It publishes computed Actor::WorldPose
and vehicle pose on /garuda/bench/truth. Never feed this topic to perception/control.
The checked scenario now tilts the camera to 0.3 rad; launch vision with
--camera-pitch .3. The harness verifies the world and metadata agree. Normal world
and vision defaults remain .12. QGC must be connected for PX4 health checks.
The harness waits for confirmed Hold after selection and confirmed Follow after
the separate command before scoring resumed samples. Original acceptance limits
remain unchanged. Full run/evidence and restoration caveats are in the run README.

Review0053 is addressed for this milestone: actor truth plugin, recorder-independent
landing confirmation, running-world identity binding, and moving plus converged
dropouts. Moving-fault run 84428c25 passes; see the current result above. The latest
scorer also explicitly checks first observed Hold against the 1-second deadline;
re-scoring that preserved log passes. Pi-link failure is still a distinct scenario,
not established by pausing vision. No shared stereo or real-flight claim.
