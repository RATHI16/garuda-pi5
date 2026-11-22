# Garuda booth bridge: yaw-only person tracking on a real PX4 drone

Add-on for [bhanuprakashjh/garuda-pi5](https://github.com/bhanuprakashjh/garuda-pi5) (tested with commit `6103e65`)
on a Raspberry Pi 5 + Hailo-10H. The drone hovers in place (position and altitude held where the pilot
switches to OFFBOARD) and only turns to face the person selected on the camera page.
None of Bhanu's files are modified except by the optional `patch_stream_lowbw.py` (see below).

## What it does
- `booth_yaw_bridge.py`: MAVLink/UDP bridge between Bhanu's on-drone command and the PX4 FC.
  - Sends `SET_POSITION_TARGET_LOCAL_NED` at 20 Hz, mask `504`: x, y, z locked at the OFFBOARD entry point,
    heading target integrated from the camera's yaw command, plus yaw-rate feed-forward (`--no-yaw-ff` = mask 2552).
  - No forward motion. Optional `--yaw-limit-deg` with return to the start heading; `--no-yaw-limit` follows all the way round.
  - Heading target never more than 15 deg ahead of the real heading; holds the heading when the command is invalid.
  - Reuses Bhanu's `fc_bridge.PiState` and `live_onboard.validate` (all of his command checks).
  - Guard rails like Bhanu's `fc_gate`: FC pinned by IP + port, advancing ATTITUDE (0.3 s) and HEARTBEAT (1.5 s)
    freshness, FC reboot detection, and LOCKOUT (sends nothing) after FC loss / disarm / bridge stall in OFFBOARD
    until the pilot leaves OFFBOARD.
  - Never arms, disarms, changes mode or writes parameters. Requires `GARUDA_FC_BRIDGE=1 --real-flight-controller`
    (or `GARUDA_SIM=1 --simulator` for loopback SITL). `--dry-run` sends nothing.
  - Keeps one local `/api/events` viewer open, because `garuda_mono.py` only detects while a viewer is connected.
  - CSV log per run in `~/booth_yaw_logs` (state, heading vs target, setpoint vs actual position, Pi command and reason).
- `test_booth_yaw.py`: 36 offline tests (`python3 -m pytest -q test_booth_yaw.py`).
- `booth_setup_once.sh`: one-time Pi setup (systemd camera service with `garuda-mono.env`, hotspot + Ethernet at boot,
  bridge copy, stream patch).
- `booth_fly.sh [dry|stop]`: checks power/heat, camera, FC ping + data, finds the FC system ID, starts the bridge in tmux.
- `patch_stream_lowbw.py`: optional local tweak to `garuda_mono.py`: smaller browser stream (`GARUDA_MONO_STREAM_W/_Q/_NEW_ONLY`),
  only when set; detection uses the full frame. Original saved as `garuda_mono.py.bhanu`.
- `windows/`: one-click launcher (`GARUDA_DEMO.bat`: dry / live / stop / watch) and one-time SSH key setup.
- `fc_sdcard/etc/extras.txt`: PX4 SD-card startup file for the onboard MAVLink link over Ethernet (UDP 14540 to the Pi).
- `BENCH_STEPS.txt`, `FLY_STEPS.txt`: step-by-step bench and flight procedures.

## Setup used
Pi 5 8 GB, AI HAT+ 2 (Hailo-10H, HailoRT 5.3.0), Arducam AR0234 on CSI (`GARUDA_MONO_CAM=csi`, depth off),
Microchip SAMV71-based FC running PX4 1.17-alpha, Ethernet MAVLink (FC 192.168.100.3, Pi 192.168.100.12),
Holybro H-Flow (optical flow + rangefinder over DroneCAN), indoor, no GPS.

## Status (honest)
- Bench and loopback tests pass; flights: heading followed the target within 1-2 deg, position held to about 4 cm.
- Altitude: held the real height within about +/-2.5 cm in a 12 s OFFBOARD segment with the rangefinder fused.
  In one FC session the EKF stopped fusing the rangefinder (height from barometer) and the real height wandered
  0.5-0.7 m. Check before every takeoff: `listener estimator_aid_src_rng_hgt` must show `fused: True`.
- Not airborne-qualified. Fly props-off checks first, then tethered, pilot on RC with mode switch and kill switch.

## Safety
The pilot is always in charge: switch to Position to leave OFFBOARD at any time (`COM_RC_OVERRIDE=3` allows stick
takeover). Recommended PX4 settings: `COM_OF_LOSS_T=1`, `COM_OBL_RC_ACT=0`. Use at your own risk.
