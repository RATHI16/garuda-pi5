# Flight test modes

The laptop lab (`http://127.0.0.1:8099`) and camera page expose explicit test modes.
Changing a mode and resetting a bench trial require fresh PX4 telemetry, GROUND,
landed and disarmed. The controller enforces this, independently of the page.

- **Bench — bounded yaw:** live Pi camera body commands into isolated PX4 SITL.
  No horizontal translation is forwarded. A trial starts on the first valid Follow
  command and requests Hold after 3 seconds or 30 degrees of accumulated absolute
  yaw travel, whichever comes first. The angle is sampled from PX4 attitude; stopping
  dynamics can produce overshoot. This is not a physical flight safety limit.
  The timer continues across interruptions. Follow, source changes and browser reloads
  cannot refund its budget. Land, then explicitly press Reset bench trial to re-arm it.
  Reset clears the controller selection and grant; explicitly select/request Follow again.
- **Gazebo — closed loop:** switches perception to the simulated onboard camera.
  Select the simulated person and explicitly start Follow. From the real-camera page,
  an applied switch disconnects that page; open the laptop lab for the simulated camera
  image and controls. This avoids pairing a real-camera image with Gazebo selections.
- **Real vehicle:** unavailable. A verified mounting/calibration/FC readiness contract
  is not yet integrated. The simulator refuses `test_mode=real`; changing a browser
  option cannot remove that restriction or route commands to real hardware.

The legacy advanced source chooser remains available. `pi_onboard` always receives
bench bounds regardless of how it was selected. Other fixed-camera/simulation sources
retain their existing behavior. No real FC bridge contract is changed here.

## Validation and installation

Offline tests cover accumulated turn (including heading wrap and reversals), elapsed
limits, latching, grounded checks, and the actual main/HTTP/tick/setpoint path with
ROS transport stubbed. Page tests cover refusing an airborne switch, waiting for the
applied acknowledgement, and disconnecting the Pi page after a Gazebo switch.

Installation requires a grounded controller restart. Code changes alone do not change
an already-running Python controller. Never describe the bounds as live until the API
reports `test_mode` and `bench_trial`. No flight trial is automatically started.
