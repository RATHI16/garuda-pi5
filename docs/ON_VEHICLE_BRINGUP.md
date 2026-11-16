# Camera on the vehicle: bring-up plan for the teams

Status 2026-11-15: code written and unit-tested on a PC; **not yet run on a Pi, a CM3, an
airframe or in Gazebo**. Every number a team measures goes into `bench/benchlog.md` with a
`bench/versions.md` snapshot; this document contains none.

This plan authorises nothing with propellers on. Stage 5 is a separate owner decision.

## 1. Three setups, one code base

| Setup | Camera | Mount | Vehicle | What it shows |
|---|---|---|---|---|
| Owner bench | OAK-D Pro | fixed | simulated (browser, or PX4 SITL lab) | stereo distance, follow in simulation |
| US team, CES static | CM3 | fixed or vehicle (on a stand) | none, nothing flies | tracking, and either the browser simulation or the command panel |
| Chennai team | CM3 first, OAK later | vehicle | real airframe, staged below | shadow command, later offboard with props off |

The camera profile is chosen when the service starts (`docs/UNIFIED_CAMERA_PROFILES.md`).
One camera per Pi at a time.

## 2. What "camera on the vehicle" does

With a fixed camera the page turns a detection into a position in the room. On a vehicle
that is meaningless, so the Pi computes a **body-frame command** instead
(`services/garuda-follow/onboard_command.py`):

| Output | Meaning | Comes from |
|---|---|---|
| `yaw_rate` (rad/s, + = turn right, clockwise from above) | turn toward the person | where the selected box is in the picture |
| `forward_vel` (m/s, + = toward the person) | close or open the gap | OAK: stereo distance minus the wanted gap. CM3/USB: box height, a size proxy, **not a distance** |
| `vertical_vel` | always 0 | altitude belongs to the flight controller |

Rules built in:

- Only a person an operator **selected** is followed. The detector's automatic pick is never
  a target.
- One distance source per camera, no fallback. OAK without a usable stereo distance: forward
  is 0 and the reason is given; turning continues.
- A one-lens box cut off by the top or bottom of the picture never commands forward.
- Any doubt gives **HOLD**: all speeds 0 and a reason (no selection, person not seen in this
  frame, frame older than 400 ms, low confidence, implausible box, identity not confirmed).
- Default limits: 0.5 rad/s turn, 0.8 m/s forward, 0.4 m/s back, 0.8 m/s² and 1.5 rad/s²
  rate of change, 2.5 m wanted gap. These are starting values, not tuned values.

**Shadow mode.** The Pi publishes the command at `/api/state` → `onboard`, marked
`transmit: false`, and the page shows it ("NOT SENT"). The detector has no MAVLink, no DDS
and no connection to a flight controller. HOLD here means "the Pi asks for zero"; it is
**not** a flight-controller Hold and must never be described as one.

## 3. Switching it on

Service drop-in for a CM3 on a vehicle (`/etc/systemd/system/garuda-mono.service.d/`):

```
[Service]
Environment=GARUDA_MONO_CAM=csi
Environment=GARUDA_MONO_SKIP_DEPTH=0
Environment=GARUDA_MONO_OAK_PAIR=0
Environment=GARUDA_MONO_OAK_BOX=0
Environment=GARUDA_MONO_MOUNT=vehicle
```

`GARUDA_MONO_MOUNT` set in the service is locked: the page cannot change it. Left unset (a
bench), the page's *Operator controls → Camera mount* can switch between the two.

Recording a walk (numbers only, no pictures, 2 minutes, at most 5 minutes): the *Record 2
min* button, or `POST /api/onboard_record?label=<a-z0-9->&seconds=<1..300>`. Files land in
`~/garuda-onboard-records/` on the Pi: one line per frame with the command, the box, the
stereo status and the Pi temperature.

## 4. Stages for a team mounting it on an airframe

Do them in order. Do not start a stage until the one before has its exit check written in
the bench log.

**Stage 0 — same software everywhere.** Same Git commit on every Pi. Record `uname -a`,
`hailortcli fw-control identify`, package versions in `bench/versions.md`. Never
`apt upgrade` to "fix" something.
*Exit:* the standard demo runs as it did on the previous release with this camera.

**Stage 1 — on the bench, camera in your hand, mount = vehicle.** Select yourself.
- Step right in the picture → "turn right". Step left → "turn left".
- OAK: walk away → "forward"; come closer than the gap → "back"; cover one stereo lens →
  forward goes to 0 with a reason, turning continues.
- CM3: walk away → "forward"; walk so close that your box touches the top or bottom → no
  forward.
- Leave the picture → HOLD at once. Return → nothing moves until you are selected/confirmed.
*Exit:* every sign is right; every loss ends in HOLD.

**Stage 2 — mounted, flight controller NOT connected to the Pi, props off, carried by hand.**
One person carries the airframe, a second is the target, a third watches the page on a
phone or laptop over the Pi's network. Record each walk.
- Power from the airframe's supply: `vcgencmd get_throttled` must stay `0x0`. An
  under-voltage flag invalidates the session.
- Temperature: this Pi has a passive heatsink and there is no prop wash while carried.
  Watch the temperature on the page; note the time to 80 °C.
- Camera tilt and field of view: at the planned height and gap, is the whole person in the
  picture? Is the horizon where you expect?
- Camera moving: turn the airframe quickly, tilt it, walk. Does the selection survive? How
  long from losing the person to HOLD? Does it ever pick up someone else? (It must not; if
  it does, stop and report with the recording.)
- CM3 has a rolling shutter: fast turns smear the picture. Note where tracking breaks.
- Motors: first with motors off. Motor vibration is a later, separate check with props off
  and the airframe restrained.
- OAK only: tape-measure the person at 2, 3 and 5 m and write down what the page shows. The
  stereo distance is unvalidated until this is done.
*Exit:* a written list of when the command was wrong or late, with recordings; power and
temperature within limits for the planned session length.

**Stage 3 — flight controller connected, receive only.** The Pi reads attitude and mode;
nothing is sent. Check that the existing offboard bridge and this stack do not both use the
link, and that timestamps make sense.
*Exit:* 30 minutes with no link errors and no effect on the flight controller.

**Stage 4 — props off, offboard, airframe restrained.** The team's bridge forwards the
shadow command. Before the first run, list every flight-controller parameter that will be
set and have it agreed (CLAUDE.md rule 12). The bridge must:
- check every field itself (finite, within its own limits, `valid` true, frame age, frame
  sequence advancing, same `instance`), and enter a defined perception-Hold when anything fails or when
  `onboard` is missing; reject future/negative ages, repeated or reversed sequences, changed
  epoch/process/target, nonzero vertical velocity and commands exceeding independent limits;
- never treat "the Pi still answers" as "the command is good";
- leave altitude and all failsafes to the flight controller.

Separate the expected outcomes **before** the run:
- Invalid perception while bridge/FC telemetry is healthy: a defined perception-Hold, with
  zero yaw as well as translation. Continuing an offboard heartbeat does not trigger PX4's
  offboard-loss failsafe. Record commanded setpoints and the actual resulting mode.
- Lost bridge/link, lost FC telemetry or expired command watchdog: stop/rescind the offboard
  stream according to the reviewed bridge policy; verify the configured FC loss action.
- Physical RC takeover must work regardless of the Pi's intent. One authorised setpoint
  publisher only; do not let this rehearsal and the team's existing bridge compete.

Test cable loss, killed detector, covered camera, frozen but responding producer, Pi/bridge
restart, changed target identity, invalid numeric fields and operator deselection. Restore
input after each fault: it must not silently re-arm or resume. Clock offsets must be bounded;
never compare unsynchronised host monotonic timestamps directly. Log receive age and
sequence progress separately. A stopped vehicle on a bench cannot prove an airborne Hold.

*Exit:* FC logs match the recorded commands, every fault matches its prewritten expected
mode/timeout, RC takeover and restart/reselection pass, and a reviewer checks the evidence.
This document is a test plan, not permission to activate the physical bridge.

**Stage 5 — anything with props on.** Not part of this plan. Needs the owner's explicit
authorisation, a tether or net, measured distance (OAK) and an agreed test card.

## 5. CES static demo (CM3, nothing flies)

- Run the release the team already knows until Stage 0 has passed on the new one.
- Mount = fixed gives the familiar browser simulation. Mount = vehicle gives the command
  panel, which is the more honest story when the camera sits on a drone on a stand.
- Keep the PX4 lab link, face enrolment and gesture control off unless rehearsed.
- The page and video are served without a password on port 8098: use an isolated network,
  never the show floor Wi-Fi.
- Rehearse for the length of a show day: temperature, restart after power loss, a crowd in
  view (many people, one selected).

## 6. Not done yet

- This code has not run on a CM3, on any airframe, or closed-loop in Gazebo.
- The follow-gap slider on the page does not change the Pi's wanted gap (2.5 m).
- No camera-tilt or attitude compensation: the command assumes a level, forward-looking camera.
- A MAVLink/UDP bridge now exists in this repository (`FC_BRIDGE.md`): written and unit-tested,
  never yet sent a setpoint. Stage 4 applies to it exactly as to the team's own bridge; run only one.
- The team's own physical bridge is the team's. The repository's separate PX4 SITL transport
  is simulation-gated; see `ONBOARD_SIM_REHEARSAL.md`. It is not a real-FC bridge.
- Team release/import plan: `TEAM_RELEASE_PLAN.md`; nothing is published by this document.
