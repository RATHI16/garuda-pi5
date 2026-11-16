# Unified camera flight controls

The Pi demo now has one setup strip: active camera, Fixed camera / On drone placement,
and Page simulation / PX4 on this laptop destination. Start PX4 lab connects the existing
controller or asks the loopback launcher on port8100 to start it. The normal flight controls
are Take off, Follow, Hold and Land. Take off does not implicitly begin following. Transport
requests, tuning, gesture controls and test signals live in Advanced tuning & diagnostics.

Fixed placement selects fixed_stereo or mono_estimate. On-drone placement selects pi_onboard
and keeps the same link, telemetry map and buttons. Follow explicitly refreshes the Pi sim
request after selection confirmation; Hold/Land still reach the controller if the Pi fails,
and clear its send request. On-drone Circle is unavailable. No fallback to another destination
is permitted. Changes of placement/destination require a fresh grounded PX4 state; an external
source change while airborne requests Hold. A disconnected page does not keep renewing its
control lease. The no-confirmation warning survives a late failed Hold reply.

The laptop launcher allows only its configured Pi origin to read/start the lab, with explicit
CORS preflight. That origin cannot reset/reconfigure the lab or proxy commands through the
launcher. Controller commands use the existing named browser lease directly on8099.
Camera-page starts enable the existing simulation-only preview/mono/onboard source flags.
A running fc_bridge process blocks launcher startup: it must not create a competing publisher.
A controller already running without required source flags reports its rejection; the UI does
not secretly restart it. The launcher itself must already be running on the laptop.

Real-flight-controller Take off/Land have no implemented API in the velocity bridge. The
normal destination selector therefore marks that option unavailable. Existing bridge bench
controls remain under Advanced diagnostics, explicitly separate from this simulator flow.
This change does not add hardware arming, mode switching or flight authorization.

Validation:184 Node page tests,335 Python follow/launcher tests,2 asset tests. Headless Chrome
with intercepted/mock endpoints exercised fixed->on-drone placement, source switching,
Take off/Follow/Hold/Land, with no page exceptions. Screenshots and logs are in
.local/unified-flight-controls-20261116. This is browser/contract evidence, not live flight.

Separately, FcGate pause fixes check Offboard exit even without camera data, check the pause
deadline on valid recovery, and latch known identity/source/mount/limit boundaries before
transient-invalid handling. Eight regression cases cover the originally reproduced paths.
The five-second policy remains experimental; no physical-flight qualification is claimed.

Initially not deployed while Claude owned the bridge session and Pi lock.
The subsequent ground-only install/check is recorded in the 2026-11-16 benchlog.
After that session is restored and released, install page assets plus mono_demo route update
and restart the laptop launcher/controller from the reviewed checkout while grounded. Run a
bounded simulator rehearsal before treating the changed page as live-validated.
