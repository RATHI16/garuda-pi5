# Live camera body commands to PX4 SITL

This is a distinct **pi_onboard** source. It is not the Gazebo-image `onboard`
rehearsal or the fixed-camera stereo/mono world-position mapping. With a bench
camera, the loop is OPEN: moving the simulated drone cannot change the physical
camera image. This can demonstrate command direction and gating, not closed-loop
following performance, physical distance accuracy or real-flight readiness.

## Starting and operating

Use the existing isolated lab setup (GARUDA_SIM=1, ROS_DOMAIN_ID=42, PX4 instance3,
system4). Add `--accept-live-onboard-in-simulation` to the controller command, or
set `GARUDA_ACCEPT_LIVE_ONBOARD=1` before starting its dashboard through
`hosts/pc/lab_processes.py`. Default is off. No new dependencies or publisher.

1. Ground/disarm the simulated vehicle. In the PC dashboard choose **Live Pi body
   command → PX4 SITL (OPEN LOOP)** and Load source. The sender begins reporting
   readiness to the Pi only while this source is selected.
2. On the camera page set vehicle mount, explicitly select a person, then choose
   **Nowhere**, followed by **PX4 on the PC**. A pre-existing enabled request is
   deliberately ignored when a sender starts or the detector restarts.
3. In the PC dashboard explicitly Take off, then Follow. This page owns a named
   control lease; keep it open. The Pi button never arms or changes PX4 modes.
4. Hold/Land remain available. After any forwarding failure, restore the cause,
   choose Nowhere then PX4 on the PC again. After lease loss, explicitly reclaim
   PC control and Follow too. Identity/re-ID target changes cannot inherit the
   current grant. Re-select the intended person when needed.

Only the existing simulator controller publishes setpoints, exclusive with its
other sources. The ordinary Pi detector remains a shadow producer, always
`onboard.transmit:false`. No physical-FC transport is included here.

## Independent checks and status

- Explicit opt-in, fresh Pi observation and distinct-frame progress <=400ms.
- Same detector instance, camera epoch, target, and distance source for a grant.
- Strict frame/command matching, person class, noncoasting lock, confidence>=0.5,
  current confirmed identity when that block is supplied.
- Finite independent limits: +0.8/-0.4m/s forward, 0.5rad/s yaw, vertical exactly0.
- OAK stereo never falls back to mono. Missing stereo range permits yaw only if
  the supplied forward command is exactly0. CM3/USB use box size, not metric depth.
- Current vehicle attitude must be near level; body forward transforms to
  horizontal NED. Existing tighter speed/travel limits still apply; reaching the
  travel boundary drops the grant and zeroes both yaw and translation.
- Browser lease, telemetry, source or send-request loss holds; a later valid
  packet on the same request generation cannot resume.
- Sender reports `sending` only after the existing controller published accepted
  setpoints. Its reader thread reports at up to4Hz; no HTTP occurs in the control
  tick. Stale controller status cannot be renewed as `sending`.

The Pi status contract must bind `generation` and detector `instance` for reports
about a request, and acknowledge `{ok,request,generation,instance}`. Old Pi builds
without that acknowledgement are refused once a grant is used. Initial readiness
registration may omit generation. Request expiry must run before heartbeat renewal.
A report must never revive an expired request or carry old `sending` status into
a different generation. These contract fixes are owned/reviewed with Claude.

## Evidence boundary

The pure validator, generation latch, status feedback, real HTTP handler and real
controller tick with stubbed ROS are tested offline. No hardware or simulator run
of this new live-Pi sender is implied by those tests. Review and a bounded live
integration check follow before calling it operational. The earlier four-fault
rehearsal used Gazebo pixels and is not evidence for this new source.
