# Flight-controller bridge (MAVLink over UDP)

Status 2026-11-16: unit-tested, and rehearsed once against **PX4 SITL on loopback with scripted
commands** (`FC_SITL_REHEARSAL.md`, evidence in the bench log): disarmed zero stream, then a
simulated flight with a turn, a forward move and six fault cases. It has **not** run on a Pi,
not with the live camera path (`main()`'s reader and status report), not with a real selected
person, and never against a real flight controller. Nothing here authorises propellers on;
see `ON_VEHICLE_BRINGUP.md` stages 3 to 5.

Seen in that rehearsal, and to be checked on any real airframe before relying on this bridge:

- **Zero vertical speed is not an altitude hold.** With the bridge commanding vertical speed 0
  in offboard, the simulated vehicle sank from about 1.4 m to about 0.7 m over some seconds.
- **PX4 went back to offboard by itself.** When the stream stopped, PX4 entered RTL after its
  configured delay. When the stream returned, PX4 re-entered offboard with no pilot action.
  The bridge was changed because of this: after a loss it now sends nothing until an operator
  starts a new stream session. That change has unit tests only; it has not been rehearsed.
- In that run the command kept flowing for about a second after the heartbeat was hidden.
  Liveness now uses fast ATTITUDE telemetry with a 0.3 s limit (proposed, unit-tested only).

`services/garuda-follow/fc_bridge.py` is the "Flight controller" target of the camera page's
send switch. It is a separate program. The detector never starts it and still sends nothing.

## What it sends, and what it cannot

| Sent | Content |
|---|---|
| `HEARTBEAT` | once a second, as an onboard computer (component 191) |
| `SET_POSITION_TARGET_LOCAL_NED`, frame `MAV_FRAME_BODY_NED` | 20 times a second: forward speed and yaw rate only (type mask 1479). Sideways and vertical speed are always 0 |

It contains no arm, disarm, mode-change or parameter message. The pilot switches to offboard.
Whether the RC can take the vehicle back, and what the flight controller does when the stream
stops, depend on the flight controller's own configuration, which this program does not read,
check or change: verify both on the airframe before relying on them. No flight-controller
parameter is read or written.

**The zero stream is a command, and it is never started for you.** The bridge sends nothing
until an operator presses **Flight controller: hold** on the camera page. From then on it
streams zero setpoints, which gives PX4 the offboard liveness it needs and commands zero
velocity. It must be the only offboard publisher for that flight controller.

## Starting it

It refuses to start unless the endpoint is stated twice, environment and command line:

```
# PX4 SITL on the same machine (loopback only; instance 3 of the bench lab is system 4)
GARUDA_SIM=1 python3 services/garuda-follow/fc_bridge.py --simulator \
    --listen 127.0.0.1:14543 --fc-host 127.0.0.1 --fc-sysid 4

# a real flight controller (props off or restrained unless the flight is authorised)
GARUDA_FC_BRIDGE=1 python3 services/garuda-follow/fc_bridge.py --real-flight-controller \
    --listen 0.0.0.0:14540 --fc-host <flight controller address> --fc-sysid <its system id>
```

- `--fc-host` and `--fc-sysid` are required: MAVLink is accepted from, and sent to, that one
  flight controller only. The first heartbeat fixes its port for the life of the process; the
  bridge never moves to another sender, even after a timeout. Another system id or port from
  that host makes the state unknown and stops the stream.
- Simulator mode refuses any non-loopback address, so it cannot reach a flight controller on
  the network. Real mode refuses a loopback address.
- `--listen`: the UDP address the flight controller sends its MAVLink to.
- `--receive-only`: read heartbeats and report to the page; never send. This is bring-up
  stage 3.
- **One setpoint publisher only.** Stop the team's own offboard program before starting this
  one. Two programs streaming setpoints to one flight controller fight each other.

## Order of operation

1. Start the bridge. It listens and reports; it sends nothing.
2. On the camera page: mount = vehicle, select the person.
3. Press **Flight controller: hold** and confirm. The zero stream starts (a stream session).
4. Pilot switches the flight controller to offboard; the vehicle holds on the zero stream.
5. Press **Flight controller: follow** and confirm. Only now can it move.

## When it stops

Two different things can end. The **grant** (the follow command) ends on any doubt about
perception; zeros keep flowing. The **session** (the stream itself) ends when contact with the
flight controller or the bridge's own continuity is lost; then nothing is sent.

| Situation | What is sent | To continue |
|---|---|---|
| Person not seen in a frame, identity not confirmed for a frame, camera state older than 400 ms at the moment of sending, no new frame for 400 ms | zero | nothing: it resumes by itself when the camera computer again reports a valid command for the SAME person, camera run and distance source, within 5 s |
| That pause lasting more than 5 s | zero | hold, then follow |
| Target or camera epoch changed, distance source changed, value outside +0.8 / -0.4 m/s or 0.5 rad/s, vertical not 0 | zero | hold, then follow |
| Mount changed, another person selected, page pressed Nowhere | zero | follow (hold first if a grant was dropped) |
| Flight controller not in offboard when follow is pressed, or leaves offboard later | zero | re-enter offboard, hold, then follow |
| No advancing ATTITUDE message from the pinned flight controller for 0.3 s, no heartbeat for 1.5 s, another id or port from its host, or its boot time going backwards | **nothing** | Nowhere, then hold (new session), then follow |
| The bridge's own loop paused for more than 0.25 s | **nothing** | Nowhere, then hold, then follow |
| Button pressed while the flight controller was silent, or before the bridge started | nothing changes; that press is used up | press again |

The 5 s pause exists because of the first live session (2026-11-16): the camera loses a
moving person for single frames all the time, and a grant that ended on the first one lasted
about 2 s. The limit is a starting value and has not been tuned or qualified.

Why the session rule exists: in SITL, PX4 was seen to go back to offboard by itself when a lost
stream returned. So after a loss the bridge does not send even zeros until an operator starts a
new session; whatever PX4 does in the meantime is its own configured loss action.

Liveness needs the flight controller to send `ATTITUDE` at 10 Hz or faster on this link. The
0.3 s limit is a proposed value; the bridge prints the longest gap it has seen every 5 s
(`attitude_max_gap_ms`) so the limit can be checked against the real link in receive-only mode.
Without that stream the bridge never sends anything.

A hold press only starts a stream if it reaches the bridge fresh (camera state no older than
400 ms) and with the camera in vehicle mount; no person is needed. A press that arrives late is
used up without effect. The bridge also says explicitly, in every bound status report, whether
it has setpoints flowing; the page shows that whatever the request is, so "follow off" never
hides a zero stream that continues, and a stopped stream is never shown as holding.

An advancing boot timestamp shows the flight controller is progressing; it does not measure
network delay and is no protection against replayed or delayed data. MAVLink signing is not
supported: signed frames and frames with unknown flags are skipped.

The page shows SENDING only from the bridge's own report that a command left this program; it
is not an acknowledgement by the flight controller and not proof that the vehicle moves. The
bridge's status line starts with SIMULATOR or REAL FLIGHT CONTROLLER.

## Not done

- Only scripted commands have been sent, and only to the simulator. The live camera path is
  the next rehearsal.
- Yaw sign and body-forward direction matched PX4 SITL telemetry in one run (positive yaw rate
  turned clockwise from above, positive forward moved along the heading). Not checked on hardware.
- The team's actual link address, port and system id are not known here.
- MAVLink 2 signing is not supported. No rate limit of its own beyond the fixed bounds above
  (the Pi's command is already rate limited).
- No systemd unit: it is started by hand on purpose.
