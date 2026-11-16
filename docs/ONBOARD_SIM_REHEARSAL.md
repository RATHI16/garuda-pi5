# On-vehicle command rehearsal in isolated PX4 SITL

This path is implemented for evaluation; a live run is not implied by passing its tests.
It has no route to a real FC. `GARUDA_SIM=1`, `ROS_DOMAIN_ID=42`, the existing isolated
Gazebo partition, PX4 instance 3 / system 4 and loopback HTTP services remain mandatory.
The existing Pi coordination lock and exclusive NPU trial runner still apply.

## What runs where

Gazebo vehicle RGB camera → PC image transport → Pi YOLO26 on Hailo → PC tracker and
explicit operator selection → Pi `onboard_command.OnboardFollower` → PC validation →
PX4 SITL velocity and yaw-rate setpoints. The extra command RPC is deliberate: the Pi
runs the same command law as the physical camera's shadow mode. There is no local CPU
fallback if the Pi command RPC is absent or fails.

The PC tracker is not the production Pi ByteTrack/re-ID path. This rehearsal cannot
validate real-camera rolling shutter, re-ID, physical distance or camera mounting.
It uses **box height only**, as a CM3-style proxy. Gazebo RGBD is not called stereo and
its distance is not fed to the command. A later stereo rehearsal needs an explicit
stereo-image path; do not relabel the simulator's ideal depth map.

The Pi binds each command request to its immediately preceding inference, once only,
and checks the selected box against those person detections. Its command reply names
the frame, epoch, target, process and selection generation. Tracking and selection remain
PC responsibilities. Control validates a selection before the reader thread sends it.
Release, reset, identity boundary or browser lease loss invalidate the generation locally
immediately; the reader clears the producer asynchronously. The consumer never waits for
that clear to hold. Reselecting the same ID starts a new limiter session even if the clear
was overtaken. Slow command RPCs run outside the vision lock; late results cannot overwrite
a subsequent pause, source or selection change.

## Preparing a controlled run

1. Pin the reviewed integration commit on both hosts, including `services/garuda-follow`.
   Archive the Pi detector wrapper, model and command-module hashes. Record prior service
   state using `sim_restoration.py`; acquire the shared Pi lock before any Pi access.
2. Start the existing `GARUDA_SIM=1 hosts/pi5/run-sim-detector.sh` trial under that lock.
   It checks cooling, owns the NPU exclusively, stops the normal detector only for the
   trial, and records what must be restored. Do not bypass its thermal aborts.
3. Use the existing loopback SSH tunnel to Pi port 8102. On the PC, run the vision service
   with `--hailo-rpc http://127.0.0.1:<tunnel-port>`. Install no new dependencies.
4. Start the isolated controller with `--accept-onboard-in-simulation` (or set
   `GARUDA_ACCEPT_ONBOARD=1` for `lab_processes.py`). Default is refusal.
5. With fresh telemetry and the simulated vehicle grounded/disarmed, load the dashboard
   source **Drone camera → Pi shadow command (mono rehearsal)**. Select a stable person
   away from frame edges. Take off, then explicitly Follow. Circle and gestures are refused.
   The existing scripted actor is independent; never move it from the controller's target.

The wanted box height is the Pi module's configured fraction. The dashboard's metre-gap
control does not change it and is disabled for this source. Speed/travel limits still
apply independently at the consumer. Climb stays in the existing takeoff/altitude loop;
command `vertical_vel` must be exactly zero. The consumer transforms forward speed into
horizontal NED using current yaw and rejects tilt over 20 degrees. Yaw rate is passed to
PX4's `yawspeed`; no invented world target is published.

## Test card fixed before a run

These are acceptance limits, not measured results:

- No command before operator selection and explicit Follow; no Circle/gesture bypass.
- Right-in-image gives positive body yaw rate; at 90-degree NED yaw forward gives east
  velocity. Limits: at most 0.8 m/s forward, 0.4 m/s backward, 0.5 rad/s turn, and the
  user's tighter speed/travel limit. At the travel boundary this first rehearsal stops
  yaw as well as translation; it may lose sight of the target there. Invalid/out-of-range numbers are rejected, not clamped
  into plausible commands. The controller may reduce speed for its own travel/speed limit.
- Command age and time since distinct-frame progress must both remain within 400 ms.
  Missing/invalid data yields zero intent on the next control tick. The control timer is 20 ms; the acceptance deadline remains 450 ms from the
  last distinct frame received by control (or from command age zero). Record controller
  tick and last-distinct-frame monotonic timestamps, as well as external observation
  latency. This leaves nominal scheduling margin without loosening the 400 ms freshness
  limit. Record actual scheduling delays; a missed limit fails the run.
- Pause the source, repeat a frame, corrupt identity, restart the RPC and revoke browser
  lease. Verify zero yaw rate as well as zero translation. A continuity loss requires a
  new selection and explicit Follow. No automatic return across restart.
- During valid loss while telemetry remains fresh, expect the controller's position Hold;
  during lost PX4 telemetry, expect FAULT and offboard heartbeat withdrawal. These are
  different events. Save both the requested setpoint and PX4's resulting mode/position.
- Log received, evaluated and sent-frame identities, source age, command fields, mode,
  attitude, altitude, actor truth **for scoring only**, temperature and throttling.
- Land and verify disarmed before stopping trial services. Confirm the trial service ended,
  then use an immutable new restoration-result filename and check the original detector's
  inference counter advances when its prior state was active. Never overwrite failures.

Offline coverage uses deterministic pixels, stub detections, real loopback RPC handlers,
the same command module, and the actual controller tick with ROS transport stubbed.
Those tests prove software contracts, not physical latency, cooling or flight quality.
