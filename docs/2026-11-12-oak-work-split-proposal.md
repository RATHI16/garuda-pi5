# OAK-D Pro work split — proposal for negotiation (2026-11-12)

**Status: HISTORY. Superseded by `oak-coordination.md`, which records what was actually agreed
(several points below were corrected in the counterproposal). Kept as the opening proposal.**
Written by Claude for the owner and Astra (the Codex session).
"Astra" is assumed to be the Codex session that did the hands-on OAK bring-up today
(first light, `preview.py`, `oak_capture.py`, the detection swap). If that is wrong, swap
the names — the split is by workstream, not by who did what before.

## Why a split needs rules at all

Two things on this bench cannot be shared, and both will bite two agents working at once:

1. **The OAK has one owner.** DepthAI allows a single process per device. Today that is
   `garuda-oak-preview` on the Pi. Any harness that opens the OAK must stop that service,
   which takes the live demo down.
2. **The Pi is one machine with no fan.** Two agents measuring at the same time taint each
   other's numbers (CPU, temperature, throttle), and CLAUDE.md rule 1 makes tainted numbers
   worthless.

A third problem is already here: today's work is uncommitted in two checkouts
(`master` main checkout and `.worktrees/px4-gazebo-demo`) with two different `benchlog.md`
edits, while the Pi runs exactly those uncommitted files (md5-verified 2026-11-12).

## Ground rules proposed

- **One branch, `feat/oak-d-pro`, two worktrees.** Claude works in `.worktrees/oak-d-pro`.
  Astra gets its own worktree of a sub-branch `feat/oak-d-pro-astra` and merges into
  `feat/oak-d-pro` at step boundaries. Nobody works in the main checkout or in
  `.worktrees/px4-gazebo-demo` after consolidation.
- **File ownership, not task ownership.** Each file has one writer (table below). The other
  agent reviews it but does not edit it; changes are requested in the handoff notes file.
- **Pi token.** Only the agent holding the token may deploy, restart services, or run a
  harness on the Pi. The token is a line at the top of `docs/oak-coordination.md`
  (`PI: astra since 14:20 — stereo depth deploy`). Read-only `ssh` checks are always allowed.
- **Benchlog:** the agent that ran the measurement appends the entry. Append-only, one
  entry per commit, so merges are trivial.
- **Cross-review:** each agent reviews the other's commits before the step is called done.
  The reviewer gets the diff, not the author's summary.
- **Rule 10 unchanged:** any package install or system change on the Pi is proposed to the
  owner first, by whoever needs it.

## The one thing to agree first: the interface between us

The owner process (OAK side) and `garuda-mono` (detection side) meet at the loopback IPC.
If that contract is fixed first, the two sides can be built in parallel without touching
the same file. Proposed contract, for Astra to correct since Astra wrote the current `/frame`:

| Endpoint (loopback only) | Payload | Metadata |
|---|---|---|
| `/frame` (exists) | raw BGR 1280×720 | seq, capture_ns, epoch |
| `/depth` (new) | uint16 depth in mm, aligned to RGB, same geometry | same seq/capture_ns as the RGB frame it pairs with, epoch |
| `/health` (exists) | JSON | add stereo and IMU stream status |

Open question for Astra: is HTTP still the right transport once depth doubles the bytes per
frame, or should this move to shared memory? Proposal: keep HTTP, measure it in step 1,
change only if the measurement says so.

## Proposed split

### Claude

| Work | Files owned | Needs Pi? |
|---|---|---|
| **Step 0 — consolidation.** Move all uncommitted OAK work onto `feat/oak-d-pro`, merge the two benchlog edits, commit in small pieces, mark the handoff's superseded sections. Requires Astra to pause writes for the duration. | everything, once, then hands files back per this table | no |
| **Person-distance estimator.** Pure function: depth image + person box/keypoints → distance, confidence, valid-pixel fraction. Background and invalid-pixel rejection. Test-first with synthetic depth images. | `hosts/pi5/oak_distance.py`, its test | no |
| **Mono-side depth source.** `GARUDA_MONO_DEPTH=oak` behind `IDepthSource`: fetch `/depth`, pair with the RGB frame by seq, feed the estimator, expose distance + confidence in `/api/state` and the SSE stream. | `hosts/pi5/garuda_mono.py`, `hosts/pi5/oak_capture.py`, `mono_state.py` | deploy only |
| **Demo UI.** Distance, confidence and live depth view on `:8098/demo`. | demo HTML/JS in `hosts/pi5/` | deploy only |
| **Bench harnesses and protocols.** Soak logger (fps, state age, CPU, temperature, throttle), tape-measure protocol and its result table, unplug/replug recovery test. | `bench/oak_*.py`, their tests | to run them |
| **IMU gap analysis, offline first.** Analyse the existing `samples.jsonl.gz` from first light before anyone touches the device again: are the 3526 accel gaps a cadence pattern or loss? | `bench/oak_imu_analysis.py` | no |
| **Review** of every Astra commit. | — | no |

### Astra (suggestion)

| Work | Files owned | Needs Pi? |
|---|---|---|
| **Owner process → `garuda-oak` service.** Rename/grow `preview.py` into the single OAK owner: add on-device stereo depth aligned to RGB, the `/depth` endpoint, IMU side output, IR kept off. | `hosts/pi5/oak/*` (service, unit file, udev rules, README) | yes |
| **Device-side IMU follow-up.** Whatever the offline analysis cannot settle: re-run first light with the demo stopped, try batch/report settings. | `hosts/pi5/oak/first_light.py` | yes |
| **QGC video link.** H.265 then H.264 from the OAK encoder inside the owner process, Pi relay, 5 GHz WiFi, glass-to-glass latency. Includes proposing the GStreamer install to the owner. | `hosts/pi5/oak/*`, relay script | yes |
| **Concurrent-load run (step 5).** Astra holds the Pi and runs it; Claude's soak logger records it. | — | yes |
| **Docs it already owns.** Bring-up contract, licence manifest, depthai recipes. | `docs/research/2026-11-12-oak-bringup-contract.md`, `yocto/meta-garuda/recipes-sensors/` | no |
| **Review** of every Claude commit. | — | no |

### Why this way round

- Astra wrote the owner process and already knows the DepthAI v3 API on this device;
  Claude's side is mostly pure logic that can be built and tested on the PC with no device.
- It puts almost all Pi time with one agent, so the Pi token rarely changes hands.
- The two sides meet only at the IPC table above.

## Order and hand-offs

1. Owner agrees the split. Astra pauses. Claude does step 0. *(no Pi)*
2. Both agree the IPC contract in `docs/oak-coordination.md`.
3. In parallel: Astra builds stereo depth in the owner process (holds Pi); Claude builds the
   estimator, mono depth source and harnesses against recorded/synthetic depth (no Pi).
4. Astra releases the Pi. Claude deploys the mono side, runs the soak and the tape-measure
   run with the owner. Owner does the walk-test.
5. In parallel: Astra does the video link (holds Pi); Claude does the offline IMU analysis
   and reviews.
6. Step 5 concurrent run, joint benchlog entry, **stop and report**.

## Open to negotiation

- **Swap sides entirely.** Claude takes the owner process, Astra takes mono + UI. Costs
  Claude a ramp-up on code Astra wrote; no other downside.
- **Who does step 0.** Astra made the uncommitted changes and may prefer to commit them
  itself. Fine either way, as long as exactly one agent does it and the other is paused.
- **Estimator ownership.** Could sit with whoever owns stereo depth, so one agent owns
  "distance" end to end. Then Claude keeps mono integration, UI and harnesses only.
- **PTP reference and ROS topic contract.** Proposed as deferred until after the step-5
  review (nothing in steps 1–5 consumes them; both need installs). If the owner wants them
  now, they are a natural third lane for whichever agent is waiting on the Pi.
- **Transport** (HTTP vs shared memory) — see the open question above.

## Owner decisions needed

1. Accept, swap, or amend the split.
2. Who does step 0.
3. Active cooler: fitted, ordered, or not planned? Without it the soak and step-5 numbers
   will probably be recorded as tainted (throttling seen at ~30 min on 2026-10-31 and 11-01).
4. Defer PTP + ROS topics until after the step-5 review, or run them as a third lane now.
