# Camera on the vehicle: bring-up plan for the teams

The owner reports that the team demonstrated Ethernet communication, Offboard entry and motor spin on a physical FC with propellers removed. This release supports mounted-camera checks and physical FC telemetry observation. The physical bridge currently permits receive-only operation. See the [engineering handoff](../hosts/pi5/demo/chennai/CLAUDE_HANDOFF.md#6-return-two-independent-records) for the evidence to return.

## 1. Three setups, one code base

| Setup | Camera | Mount | Vehicle | What it shows |
|---|---|---|---|---|
| Owner bench | OAK-D Pro | fixed | simulated (browser, or PX4 SITL lab) | stereo distance, follow in simulation |
| US team, CES static | CM3 | fixed or vehicle (on a stand) | none, nothing flies | tracking, and either the browser simulation or the command panel |
| Chennai team | CM3 first, OAK later | vehicle | real airframe, staged below | grounded camera measurements and receive-only FC telemetry |

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

**End of the physical test path for this release.** Return the camera measurements and the receive-only FC record for independent review. An absent or stale FC report should show unknown. Retain the FC-side log alongside the observer log and record how the clocks were compared. Command-loss behavior, pilot takeover and orbit in the air still require separate vehicle-specific qualification.

## 5. CES static demo (CM3, nothing flies)

- Run the release the team already knows until Stage 0 has passed on the new one.
- Mount = fixed gives the familiar browser simulation. Mount = vehicle gives the command
  panel, which is the more honest story when the camera sits on a drone on a stand.
- Keep the PX4 lab link, face enrolment and gesture control off unless rehearsed.
- The page and video are served without a password on port 8098: use an isolated network,
  never the show floor Wi-Fi.
- Rehearse for the length of a show day: temperature, restart after power loss, a crowd in
  view (many people, one selected).

## 6. Interpreting the result

A passing mounted-camera check covers that camera, mount and test conditions. The physical bridge in this tag is receive-only. The combined camera/aircraft system has no airborne acceptance for height hold, target loss or orbit. Use the [simulator test](TESTING_2026-11-18.md#5-rehearse-the-gui-in-gazebo) for operator controls and the [release record](RELEASE_2026-11-18.md) for the measured scope.
