# What is new — November 2026

This is the guide for someone who has just downloaded the repository and wants to know what
the camera demo can now do, how to use each feature, and how far each one has been tested.

Read the status lines literally. "Tested in simulation" means PX4 SITL and Gazebo on a
laptop. **Nothing in this release has flown a real aircraft, and nothing has been sent to a
real flight controller.** Measured numbers live in `bench/benchlog.md`; this guide gives
settings and limits, not performance claims.

| # | Feature | Where it runs | Status |
|---|---|---|---|
| 1 | [OAK-D Pro stereo camera](#1-oak-d-pro-stereo-camera) | Pi | working on the bench; distance not calibrated |
| 2 | [Three camera profiles](#2-three-camera-profiles-one-code-base) | Pi | OAK and USB run on the bench; CM3 not re-run on this release |
| 3 | [Selecting and keeping one person](#3-selecting-and-keeping-one-person) | Pi + page | working on the bench with one person |
| 4 | [Named faces](#4-named-faces) | page | written and unit-tested; not tried with a real face |
| 5 | [Camera placement: fixed or on the drone](#5-camera-placement-fixed-or-on-the-drone) | Pi + page | working on the bench |
| 6 | [The follow command computed on the Pi](#6-the-follow-command-computed-on-the-pi) | Pi | working on the bench; gains untuned |
| 7 | [One setup strip and four flight buttons](#7-one-setup-strip-and-four-flight-buttons) | page + laptop | installed; checked on the ground |
| 8 | [PX4 simulator on the laptop](#8-px4-simulator-on-the-laptop) | laptop | tested in simulation |
| 9 | [Test modes](#9-test-modes) | laptop | bench mode installed; real-vehicle mode unavailable |
| 10 | [Send switch](#10-send-switch-who-may-transmit) | Pi | tested live against simulated senders |
| 11 | [MAVLink flight-controller bridge](#11-mavlink-flight-controller-bridge) | Pi or laptop | tested against the simulator only |
| 12 | [Recording a carry-around](#12-recording-a-carry-around) | Pi | written and unit-tested; not used yet |
| 13 | [Bring-up plan for teams](#13-bring-up-plan-for-teams) | document | plan only |

Start the page at `http://<pi address>:8098/demo`. On the owner's bench the Pi is
`10.42.0.128` and the laptop lab is `http://127.0.0.1:8099`.

---

## 1. OAK-D Pro stereo camera

**What it is.** The OAK-D Pro supplies a colour image and two global-shutter mono images. The
OAK does no depth and no AI: the Pi rectifies the mono pair and matches it (OpenCV SGBM), and
the Hailo-10H runs the detector. Stereo is matched only inside the selected person's box, so
it costs little.

**What you see.**
- A distance badge on the live view for the selected person, marked UNVALIDATED.
- **Depth map** button: red is near, blue is far, black is no depth. The white box is the
  region that was measured.
- **3D view** button: a point cloud of the scene you can rotate.
- Hardware H.265 video from the OAK to QGroundControl on the laptop (UDP port 5600).

**Status and limits.** The distance has not been calibrated against a tape measure and must
not be quoted as a measurement. Close to the camera the distance is sometimes refused
(`cluster_too_spread`); when that happens the badge shows a dash and nothing uses a guess
in its place. The IR projector and flood light stay off.

**Read more.** `docs/OAK_HOST_STEREO.md`, `docs/OAK_CALIBRATION_WORKSPACE.md`.

## 2. Three camera profiles, one code base

One Pi uses one camera at a time. The camera is chosen when the detector service starts.

| Profile | Service setting | Distance the demo uses |
|---|---|---|
| OAK-D Pro | `GARUDA_MONO_CAM=oak`, `GARUDA_MONO_OAK_BOX=1`, `GARUDA_MONO_SKIP_DEPTH=1` | stereo |
| Camera Module 3 | `GARUDA_MONO_CAM=csi`, OAK settings 0, `GARUDA_MONO_SKIP_DEPTH=0` | one-lens estimate (not a measurement) |
| USB webcam | `GARUDA_MONO_CAM=/dev/videoN`, OAK settings 0, `GARUDA_MONO_SKIP_DEPTH=0` | one-lens estimate (not a measurement) |

The detector tells the page what the camera can honestly offer
(`/api/state` → `stats.capabilities`), and the page words itself accordingly. Stereo and the
one-lens estimate are separate sources. One is never a fallback for the other.

**Status.** OAK is the standing bench profile. The USB profile was run for the first time on
2026-11-16 for about a minute and worked, at roughly half the OAK profile's frame rate (not
investigated). The CM3 profile has not been run on this release, because the bench has no
working CM3; teams with a CM3 must do a first run themselves.

**Read more.** `docs/UNIFIED_CAMERA_PROFILES.md`.

## 3. Selecting and keeping one person

**How to use.** Click a person's box on the live view. The page shows "SELECTED #n". Only a
person someone clicked is ever followed; the detector's own automatic pick is never a target.

**Keeping the same person.** A re-identification network (OSNet, evaluation-only licence, not
cleared for a product) compares appearance, so when the tracker loses and re-finds someone the
demo can tell whether it is the same person. The chip under the picture says
"SAME PERSON · CONFIRMED" or says that it is checking. While identity is not confirmed,
nothing follows.

**Status and limits.** Works on the bench with one person in view. Two people swapping places
has not been recorded and scored. The embeddings are kept in memory only.

## 4. Named faces

**How to use.** With a person selected, press **Enroll selected face…** and type a name. The
name then appears on that person's box and target button when the face is recognised.
**Forget all faces** clears them. **Face-confirmed return** makes a returning person need a
face match before follow resumes.

**Where the data is.** Names and face samples are stored in that browser only
(`localStorage`). Nothing is uploaded and nothing is written into Git.

**Status.** Written and unit-tested. Not yet tried with a real face.

## 5. Camera placement: fixed or on the drone

A camera on a stand and a camera bolted to the drone need different things.

- **Fixed camera** (bench, stand): a detection is turned into a position in the room, and a
  simulated vehicle follows that position.
- **On drone**: positions in the room mean nothing, because the camera moves with the drone.
  The Pi computes a steering command instead (feature 6).

**How to use.** Choose **Placement** in the setup strip. A team can lock it in the service with
`GARUDA_MONO_MOUNT=vehicle` (or `fixed`); when it is set there the page cannot change it. API:
`POST /api/mount?value=fixed|vehicle`.

## 6. The follow command computed on the Pi

With placement **On drone**, the Pi works out what a vehicle should do to follow the selected
person (`services/garuda-follow/onboard_command.py`) and publishes it in `/api/state` →
`onboard`.

| Output | Meaning | Comes from |
|---|---|---|
| turn rate (rad/s, + = turn right) | turn toward the person | where the box is in the picture |
| forward speed (m/s, + = toward the person) | close or open the gap | OAK: stereo distance minus the wanted gap. CM3/USB: box height, a size cue, **not a distance** |
| vertical speed | always 0 | height belongs to the flight controller |

Built-in limits (starting values, not tuned): 0.5 rad/s turn, 0.8 m/s forward, 0.4 m/s back,
wanted gap 2.5 m, command ignored if the frame is older than 400 ms.

**It holds (all zero, with a reason)** when nobody is selected, the person is not seen in this
frame, the frame is old, confidence is low, the box is implausible, or identity is not
confirmed. With an OAK and no usable stereo distance, forward is 0 and turning continues.

**The Pi itself never transmits.** The command is marked `transmit: false`. A separate sender
forwards it (features 8 and 11) and only when an operator asks (feature 10).

**Known gaps.** The follow-gap slider on the page does not change the Pi's wanted gap. There
is no compensation for camera tilt or vehicle attitude: the command assumes a level,
forward-looking camera.

## 7. One setup strip and four flight buttons

The page has one strip at the top:

- **Camera**: what is connected (for example "OAK-D PRO · STEREO").
- **Placement**: Fixed camera / On drone.
- **Control destination**: Page simulation (a drawing in the browser) / PX4 on this laptop.
- **Start PX4 lab**: connects to the simulator on the laptop. The laptop's launcher
  (`http://127.0.0.1:8100`) must already be running if the controller is not running.
  An existing controller on port 8099 is reused.

and four flight buttons: **Take off, Follow, Hold, Land**. Take off does not start following.
For the on-drone live-camera path, Hold and Land are sent to the laptop controller before
attempting to turn off the Pi request, so a Pi request failure does not block that attempt.
Delivery still requires a working laptop/controller connection; a queued request is not
proof that PX4 executed it.

The demo's own controls (follow gap, travel and speed sliders, follow position, gesture
control, face enrolment) stay in the main view. Transport plumbing and bench tools are under
**Advanced tuning & diagnostics**; floor calibration and the placement recorder are under
**Operator controls & calibration**.

**Leaving the lab link always works.** If the simulator cannot confirm that its vehicle is
down or holding, the page still disconnects and says "Unlinked WITHOUT confirmation". Choosing
**On drone** while a fixed-camera link is up first disconnects that link. Placement and
destination changes require a fresh grounded/disarmed lab state; they are refused while
airborne or while that state is unknown. Disconnecting the page is not proof of a landing.

**Read more.** `docs/UNIFIED_FLIGHT_CONTROLS.md`, `docs/DEMO_USER_GUIDE.md`.

## 8. PX4 simulator on the laptop

The laptop runs PX4 SITL, Gazebo and QGroundControl in an isolated setup
(`GARUDA_SIM=1`, ROS domain 42). The lab page is `http://127.0.0.1:8099`.

| Source | What drives the simulated drone | Loop |
|---|---|---|
| OAK stereo distance (fixed camera) | the person's position from stereo | open: the real camera does not move |
| CM3 / USB one-lens estimate (fixed camera) | a schematic position from box size | open |
| Live Pi body command (on drone) | bounded yaw from the Pi command; no horizontal translation (feature 9) | open bench trial |
| Drone camera → Pi shadow command | Gazebo's drone camera, detected on the Pi's Hailo | closed, in simulation |
| Drone camera · Gazebo / PC | Gazebo's camera and the PC's detector | closed, in simulation |

The `fixed_stereo`, `mono_estimate` and `pi_onboard` paths require their explicit
`--accept-…-in-simulation` flags. The legacy `pi` source is a separate default path.
The page launcher enables the simulation flags for the controller it starts; these flags
do not authorise a real vehicle.

- **Control lease.** Only one browser page commands the simulated drone at a time. If that
  page stops renewing its lease, the running lab controller requests Hold after about
  three seconds. This is not a guarantee if the controller itself has stopped or PX4
  telemetry is stale; PX4's configured offboard-loss behavior then matters.
- **Command monitor.** The lab page shows the last setpoint it published (velocity, position,
  yaw rate, count, age). It shows what was sent, not proof of receipt.

**Status.** Take-off, follow, hold-on-loss and landing have passed in simulation for the
Gazebo-camera paths, and a four-fault test (wrong person, frozen frame, detector restart, lost
browser) passed across separate runs. The live-camera paths are open loop by nature and show
direction and gating, not following quality.

**Read more.** `docs/LIVE_PI_SIM_SENDER.md`, `docs/ONBOARD_SIM_REHEARSAL.md`,
`docs/FIXED_STEREO_OBSERVATION.md`, `docs/px4-demo-isolation.md`.

## 9. Test modes

Chosen on the laptop lab or under **Test modes & turn limits** in the camera setup strip.
Mode changes and trial resets require fresh telemetry, GROUND, landed and disarmed; the
controller checks these conditions as well as the page. A mode change clears controller
selection and Follow permission. Select the appropriate person and start Follow explicitly.

- **Bench — bounded yaw.** The live camera's command turns the simulated drone, with no
  horizontal translation. The timer starts on the first valid forwarded Follow command.
  The controller requests Hold after 3 seconds or 30 degrees of accumulated absolute turn,
  whichever comes first; reversals do not refund the angle budget. Sample timing and vehicle
  stopping dynamics can cause overshoot. Follow clicks, source switches and page reloads
  do not reset the budget. Land, disarm and press **Reset bench trial** to run another;
  selection/request and Follow must be made explicitly again.
- **Gazebo — closed loop.** Switches to the simulated drone's own camera, so the loop closes.
  After a confirmed switch from the camera page, that page disconnects. Open the laptop
  lab to see the simulated camera, select its person and use its flight controls.
- **Real vehicle.** Not available. The option is disabled and the simulator refuses it.

**Status.** Bench bounds are installed and the grounded state/API and browser layout were
checked. The limit was tested through the actual HTTP/controller/setpoint path with ROS
transport stubbed; a flight exercising the newly installed bound has not been recorded.
Real-vehicle readiness checks are not implemented, rather than merely awaiting a toggle.

**Read more.** `docs/FLIGHT_TEST_MODES.md`.

## 10. Send switch: who may transmit

The Pi keeps one small record of where the operator asked the on-drone command to go, and of
what each sender says it is doing. It is the single place that answers "is anything being
sent right now?".

| Request | Meaning |
|---|---|
| `off` | follow command not forwarded |
| `sim` | forward to the PX4 simulator on the laptop |
| `fc_hold` | ask the flight-controller bridge to start its "stay still" stream |
| `fc` | ask the bridge to forward the follow command |

Rules worth knowing:

- A request is accepted only if that sender reported in within the last 2 seconds, and only
  with placement On drone.
- It falls back to `off` when placement changes, another person is selected, the sender goes
  silent, or the detector restarts. It never returns by itself.
- The page shows "sending" only from the sender's own fresh report, tied to the exact request
  and detector process. The bridge also says explicitly whether any stream is flowing, so
  "follow off" never hides a "stay still" stream that continues.

API (for people writing a sender): `POST /api/transmit?target=off|sim|fc_hold|fc`,
`POST /api/transmit_status?target=sim|fc&state=ready|sending|holding|refused&reason=…&generation=N&instance=…&stream=1|0`,
state in `/api/state` → `transmit`.

## 11. MAVLink flight-controller bridge

`services/garuda-follow/fc_bridge.py` forwards the Pi's follow command to a PX4 flight
controller over MAVLink/UDP. It is a separate program, started by hand.

**What it can and cannot send.** Its own heartbeat, and a body-frame velocity setpoint carrying
forward speed and turn rate only. It contains no arm, disarm, mode-change or parameter message.
The pilot arms, takes off and switches to offboard. No extra software is needed: the three
MAVLink messages are written in plain Python and checked against pymavlink in the tests.

**Starting it.** It refuses to start unless told twice what is at the other end, and which
one flight controller it may talk to:

```
# PX4 simulator on the same machine (loopback only)
GARUDA_SIM=1 python3 services/garuda-follow/fc_bridge.py --simulator \
    --listen 127.0.0.1:14543 --fc-host 127.0.0.1 --fc-sysid 4 --source http://<pi>:8098

# a real flight controller: props off or restrained unless the flight is authorised
GARUDA_FC_BRIDGE=1 python3 services/garuda-follow/fc_bridge.py --real-flight-controller \
    --listen 0.0.0.0:14540 --fc-host <its address> --fc-sysid <its system id>
```

Add `--receive-only` to listen and report without ever sending.

**Order of use.**
1. Start the bridge. It sends nothing.
2. On the page: placement On drone, select the person.
3. Ask for **hold** (`fc_hold`). The bridge starts streaming "stay still".
4. The pilot switches the flight controller to offboard.
5. Ask for **follow** (`fc`). Only now can the vehicle move.

**When it stops.**

| Situation | What it does |
|---|---|
| The camera loses the person for a moment, or one frame arrives late | sends "stay still"; resumes by itself when the same person is confirmed again, for up to 5 seconds |
| The person is gone for more than 5 seconds | follow ends; ask for hold, then follow again |
| Another person, another camera run, a value outside its limits, placement changed | follow ends at once |
| The pilot takes the vehicle out of offboard | follow ends at once |
| The flight controller's fast telemetry stops for 0.3 s, or the bridge itself stalls | sends nothing at all, and stays silent until someone asks for hold again |

Why it goes fully silent after losing the flight controller: in simulation, PX4 was seen to
return to offboard by itself when a lost stream came back. The bridge must not be the thing
that pulls it back.

**Requirements on the flight controller's link.** It must send `ATTITUDE` at 10 Hz or faster.
MAVLink signing is not supported.

**Status.** Tested against the PX4 simulator only: correct packet form, correct turn and
forward direction, six fault cases, and one live session in which a real person in front of
the camera steered the simulated drone. Not run on a Pi as a service. Never connected to a
real flight controller.

**Before any real airframe, check these, seen in simulation:**
- Commanding zero vertical speed did not hold height: the simulated drone sank after
  hand-over to offboard.
- What PX4 does when the stream stops, and whether the RC can take over, depend on that
  flight controller's own configuration, which this program does not read or change.
- Only one program may stream setpoints to a flight controller. Stop any other offboard
  program first.

**Read more.** `docs/FC_BRIDGE.md`, `docs/FC_SITL_REHEARSAL.md`. The rehearsal tools are
`bench/fc_sitl_rehearsal.py` (scripted commands) and `bench/fc_live_ground.py` (real Pi path,
ground only); both print their plan and send nothing unless run with `--execute`.

## 12. Recording a carry-around

With placement On drone, **Record 2 min** (under Operator controls) or
`POST /api/onboard_record?label=<name>&seconds=<1..300>` writes one line per frame to
`~/garuda-onboard-records/` on the Pi: the command, the box, the stereo status and the Pi
temperature. Numbers only, no pictures.

**Status.** Written and unit-tested; not used in a real carry-around yet.

## 13. Bring-up plan for teams

`docs/ON_VEHICLE_BRINGUP.md` is the staged plan for a team mounting the Pi and camera on an
airframe: same software everywhere, camera in the hand, carried on the airframe with the
flight controller disconnected, flight controller connected receive-only, then offboard with
props off and the airframe restrained. Anything with props on is outside the plan and needs
the owner's explicit authorisation. `docs/TEAM_RELEASE_PLAN.md` describes how a release is
handed to the sites.

---

## What is not validated

- Stereo distance against a tape measure. One-lens "distance" is never a measurement.
- Following two or more people who cross.
- The CM3 profile on this release.
- Any closed-loop following with a real camera: a camera on a desk does not move with a
  simulated drone.
- Height hold while the bridge is in control.
- Everything involving a real flight controller or a real flight.
- Cooling: the bench Pi has a passive heatsink and has reached its temperature limit during
  long sessions.

## Privacy and network

The page, the video and the controls are served without a password on port 8098. Use an
isolated network. No camera images are kept in Git; face samples stay in the browser that
enrolled them.

## Licences

Detector and pose models from the Hailo model zoo and the OSNet re-identification model are
evaluation-only and not cleared for a product. Model files are not part of the repository;
see `models/` for how to fetch them.
