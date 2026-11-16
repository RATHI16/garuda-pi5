# Shared camera profiles and simulation controls

## Scope and status

One codebase supports one active camera per device. Different colleagues can use different
profiles on their own devices. This is an initial deployment choice, not a Hailo hardware
limit. The owner reports CM3 cameras in use by colleagues; the old local electrical-failure
note does not establish the state of their cameras.

OAK supplies images and video encoding. Pi host stereo and Hailo inference stay host-side.
CM3 and USB mono follow use an explicitly selected `mono_estimate` simulator source.
`fixed_stereo` never falls back to mono. Physical accuracy and real flight remain unvalidated.

## Mono simulation today

Start the lab controller with `--accept-mono-estimate-in-simulation`, or pass
`GARUDA_ACCEPT_MONO_ESTIMATE=1` to the existing lab process launcher. Select **CM3 / USB mono
estimate (schematic)** and load that source while grounded with fresh telemetry. Select a
visible person, then explicitly request Follow. The producer must publish
`stats.capabilities.distance_source = mono_estimate` and valid frame/process identity.

The first implementation maps person box height and horizontal centre into a bounded virtual
position. It does not turn monocular model output into calibrated metres. The mono distance
readout remains separate. Missing/truncated boxes, stale frames and identity discontinuity
cause Hold; after a continuity gap the operator reselects. No inferred physical standoff,
collision avoidance, camera motion compensation or airborne camera localization is claimed.

CM3 service profile overrides (configuration examples only; not installed here):

```ini
GARUDA_MONO_CAM=csi
GARUDA_MONO_SKIP_DEPTH=0
GARUDA_MONO_OAK_PAIR=0
GARUDA_MONO_OAK_BOX=0
```

For USB, replace `csi` with the enumerated `/dev/videoN` for that device. Do not use `auto`
when promising a particular camera. Preserve the device's reviewed detector/depth/re-ID model
paths and runtime configuration. The standing OAK profile's `GARUDA_MONO_SKIP_DEPTH=1` must
not carry into this mono profile: it reports no mono distance capability and the explicit
mono simulator source correctly refuses it. The mono engine and its model must already be
installed; these examples are not an installation or hardware validation result.

## Browser controller ownership

Each tab uses a new random `X-Garuda-Session`. An explicit command acquires an available
3-second lease; `POST /api/heartbeat` with JSON `{}` every second renews that existing lease.
The owner ID is visible in state. This is an accidental multi-tab contention guard, not an
authentication mechanism; it is only intended for the existing loopback bench simulator.
GET polling does not renew it. Heartbeats cannot claim expired ownership. Competing commands
are refused, except Hold/Land, which do not transfer ownership. Expiry forces Hold and disables
gesture commands; explicit control must be requested again. A queued command cannot execute
under another lease. A Web Worker sends the linked page's heartbeat with one request in flight and a 900 ms
request timeout. This tracks page/worker/network presence, not the health of the main UI
thread. Behavior while covered/backgrounded and after closing a real browser tab remains a
live-browser acceptance check; unit tests do not establish it.
Legacy headerless harness commands work before any browser claim; after
that, restart a grounded controller for a separate headless harness session.

`POST /api/command` returns a command ID and `queued: true`. State publishes
`control.last_command = {id,name,applied,error}` after execution by the controller handler.
This is not a PX4 acknowledgement. A page must verify fresh telemetry and `behavior: hold`
before claiming it has safely unlinked. Once the page has contacted the lab or sent a command,
loss of contact stays visibly linked/unknown, including across reload. Hold before an arm
request cancels startup. If an arm request is already in flight, Hold cancels the climb goal
at the current fresh pose; it does not mean disarmed. Land remains the explicit way out. Unknown telemetry is never disarmed confirmation.

## Profile switching design (not installed)

Profiles: `oak_host_stereo`, `cm3_mono`, `usb_mono`. A shared transition protocol should:

1. Confirm actual controller state is fresh, landed and disarmed. Never trust one page's
   linked checkbox. An active but unreachable controller blocks switching.
2. Acquire the device transition lock, record current profile and service states, request Hold,
   and invalidate selection, process/frame epoch, identity gallery and cached distances.
3. Stop the old capture/detector workers before opening another camera. OAK retains one owner.
4. Apply only an allowlisted profile, start its workers, wait a bounded time for new frames and
   capability metadata. Missing hardware fails visibly; it does not choose a different camera.
5. On failure restore the recorded profile/service state, verify fresh frames, retain the failed
   attempt evidence. If restoration fails, show unavailable. No automatic follow resume.

Use this same transaction for all profiles before considering in-process hot switching.
Until implemented/reviewed, profiles are selected at service start. A privileged helper, if
needed, accepts profile IDs only, not shell commands or file paths from the browser. No helper
has been installed as part of this change.

## Shared Git for the team

This development checkout uses `git@github.com:bhanuprakashjh/garuda-sdk-bench.git`.
The owner has also published a Pi-specific import at `git@github.com:bhanuprakashjh/garuda-pi5.git`
(`/tmp/pi5repo`, initial import `f19835a`). These are distinct histories; do not assume our
new commits are already in the colleague-facing Pi repository. Prepare a reviewed export or
integration into that repository before giving colleagues a release SHA. Do not maintain
both as independently edited sources of the same Pi application.
Keep application code, profile templates, tests, model-fetch instructions and schema contracts
there. Use short feature branches and a reviewed integration release, with the commit pinned
on each colleague's device. Do not maintain separate application forks for each camera.

Keep each device's addresses, camera path/index, calibration, model location and service
drop-ins outside tracked source. Do not commit `.local`, `.runtime`, recordings, HEF binaries
or credentials. Publish model checksums/version requirements, not ad-hoc copied model files.
A handoff should record Git SHA, profile, OS/Hailo versions, model hashes and calibration status.
This work does not push or merge the development branches; review is still required before a
shared release. Existing unrelated changes in the main checkout are preserved.

## Acceptance checklist

- Offline: lease expiry/competition, queued old commands, explicit resume, source rejection,
  stale/restarted identity, finite/bounded mono boxes, stereo no-fallback and existing tests.
- Browser: linked map uses telemetry; Pause reaches Hold; unlink requires fresh Hold;
  lost link does not turn into a browser-only drone; capabilities/readouts match each profile.
- Hardware later: CM3/USB enumeration and fresh frames, Hailo detection/re-ID load, missing
  camera rollback and restart epoch; multi-person recordings and calibrated distance checks.
- Simulation later: commanded motion, disconnect Hold, explicit recovery, source switch
  refusal while armed, landing and restoration, with recorded evidence.

Passing offline tests is not evidence that hardware or flight tests have passed.
