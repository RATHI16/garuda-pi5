# Bounded FC bridge rehearsal

Current runner is updated for the fc_hold stream-session contract. **This revision has not
been run against SITL.** Earlier ground/airborne results use ed17253 and the previous
bridge; they do not validate the new session/liveness behavior. Await bridge findings in
astra-0121 and peer review of this runner before execution.

Default `python3 bench/fc_sitl_rehearsal.py` prints plan, preset limits and current bridge
hash without opening sockets. Execution requires GARUDA_SIM=1, --execute, exact
--controller-pid, --coordination directory, a NEW --output directory and
--reviewed-bridge-sha256. --airborne is separate and is not implied by a ground-stage run.
No physical FC address is accepted. The runner uses scripted camera-command records and
the production Bridge object, not live perception or fc_bridge.main's HTTP/status thread.

The runner first requires fresh grounded/disarmed state and no operator lease. It checks
controller environment/domain42 and other known controller/bridge processes, acquires the
coordination lock and stops only the specified controller. This scan does not prove that
an arbitrary third-party publisher is absent; inspect QGC/other tooling before execution.
Loopback14543 and system4/component1 are pinned. Every outgoing bridge packet is decoded;
frame8/mask1479, zero side/vertical, <=.201m/s forward and <=.151rad/s yaw are required.

## Ground stage

Receive-only15s sends nothing and measures advancing ATTITUDE at the receiver. Require
at least100 advancing samples and maximum observed gap<=300ms; this is a cadence check
against the proposed bound, not a claim of source-to-host latency. No heartbeat-only
fallback. Receiving is sampled by the runner, so gaps include polling/processing effects.
Then sending mode with no session must remain silent. An explicit off->fc_hold generation
opens the zero stream; two seconds of zero packets are decoded and cadence checked.
Actual cadence is reported, never assumed20Hz (50ms sleep plus work runs slightly slower).

## Airborne stage, separately reviewed

A separate allowlisted command-only tool requests parameter READS, takeoff/mode/land;
the bridge cannot arm or change modes. No parameter writes. Log six configured failsafe/
takeoff parameters and every COMMAND_ACK. Reject takeoff altitude outside0.8–4.5m before
arming. Wait for the configured takeoff height within0.20m and |vz|<=0.10m/s continuously
for2s; missing telemetry resets settling. Only then request Offboard. From handover until
the final deliberate FC-loss observation, require height within±0.20m of handover, plus
3m horizontal radius and5m altitude envelope. If it fails, record and land—do not widen.

Commands are capped at .2m/s and .15rad/s for2s. PX4 telemetry scores positive yaw0.08–0.7rad,
forward displacement0.08–0.8m/cross-axis<=0.3m. These are PX4 estimates, not inherently
simulator ground truth; compare ULog groundtruth separately when interpreting results.

Perception identity/missing/frozen faults must zero the yaw channel within450ms; markers
are logged before ticking. Zeros must continue, with no old-grant recovery. A300ms loop
pause must end the session and produce no packets after resumption; .25s silent observation
is followed by a NEW explicit hold session and explicit Offboard request before continuing.
Mode exit uses AUTO/LOITER, then explicit Offboard; old follow grant must remain gone.

The FINAL fault suppresses ATTITUDE only, while HEARTBEAT and scoring telemetry continue.
The first stopped state must be reached by350ms observation (300ms liveness+50ms tick).
Record observed timing rather than claim exact300ms cutoff. Require no packets, then a
bounded3s observation of PX4's configured loss action; the handover height band is disabled
for this deliberate action, but the overall5m/3m envelope remains. Record actual mode.
Restore attitude packets; the SAME request must not restart the stream. No further motion
phase follows. All zero/session-silence assertions require actual packet/state evidence.

## Cleanup and evidence

New immutable evidence directory; append-only packet/telemetry JSONL plus manifest/summary.
Failures stay failures. Cleanup clears synthetic motion and fault injection, keeps receiving,
sends LAND on armed OR unknown state to the already pinned simulator, retries every2s and
waits up to45s. Airborne runs need disarmed AND landed; missing state is not a pass. No force
disarm. After grounding, close bridge socket, restart the original controller with its saved
environment in memory, update launcher PID/start identity, restore source/settings, release
lock. If grounding/restoration is unconfirmed, retain the lock for recovery and do not
silently restart another publisher. This is simulator-only, not physical flight readiness.

R1 scoring decision before any revised airborne run: retain the same height reference and
±0.20m band during intentional loop-stall/recovery. Mark that interval separately, logging
last setpoint, first resumed setpoint, measured stream gap and heartbeat mode. A band breach
there is a deliberate loss/recovery-window failure, not evidence of steady height-hold
failure. It still fails and lands; no widened band or hidden rebaseline. The separate label
ends only after explicit session, explicit Offboard and two seconds of recovery observation.

## Real Pi HTTP/status ground-only runner

`bench/fc_live_ground.py` defaults to plan-only. Execution requires the same exact PID,
coordination/output/hash arguments and GARUDA_SIM=1. It starts the actual bridge CLI with
--source http://10.42.0.128:8098, not scripted input. A two-socket relay binds loopback
14543/14644 and forwards only to pinned SITL14583 and bridge14643. The relay independently
requires fresh disarmed PX4 heartbeat and allows only source4/191 heartbeat or ZERO
frame8/mask1479 setpoints for4/1; any arm/mode/nonzero packet aborts before forwarding.
No flight tool exists in this runner. The Pi must start fixed/off and is restored fixed/off;
changing mount clears selection, explicitly part of this trial. No images are recorded.

Receive-only must show a real listener and refuse fc_hold without UDP output. Exit must
expire the listener/request. Sending main() must remain silent until a new hold, then wire
zeros must match bound streaming status. Follow-off must truthfully show continuing zeros.
A400ms SIGSTOP/SIGCONT must end the session and keep it silent until explicit off/hold.
Finally process exit must stop packets and expire the Pi listener; restore PC settings/PID
record and release lock. This is a hold-only contract test: no real person selected, no
follow grant or live perception-to-motion validation. It does not test the real FC network.
