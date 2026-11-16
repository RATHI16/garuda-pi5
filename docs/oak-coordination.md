# OAK-D Pro — two-session working agreement and `/pair` contract

Agreed 2026-11-12 between the Claude session and the Codex session ("Astra"), with the owner's
in-session approval of the role split, the consolidation and the PTP/ROS deferral.
History of how this was reached: `2026-11-12-oak-work-split-proposal.md`,
`2026-11-12-oak-work-split-counterproposal.md`, and the mailbox messages `claude-0001`,
`astra-0001`, `claude-0002`. **This file is the decision; those are history.**

Nothing in this file is a measurement. Every number below is a protocol limit or a provisional
design parameter and is labelled as such.

## 1. Roles and file ownership

One writer per file. The other session reviews the commit diff and never edits the author's files.
Neither session marks its own work cross-reviewed.

| Owner | Work | Files |
|---|---|---|
| Claude | Consolidation; person-distance estimator; mono-side depth source; demo UI; offline IMU analysis; host-side harnesses (soak, tape-measure, unplug/replug) | `hosts/pi5/garuda_mono.py`, `hosts/pi5/oak_capture.py`, `hosts/pi5/mono_state.py`, `hosts/pi5/oak_distance.py` (new), demo HTML/JS, `bench/oak_*.py` |
| Astra | The single OAK owner process; stereo output and pairing; device-side IMU follow-up; codec/relay for the QGC video link; owner-service tests; running the final concurrent test | `hosts/pi5/oak/*`, `docs/research/2026-11-12-oak-bringup-contract.md`, `yocto/meta-garuda/recipes-sensors/` |
| Claude as integrating writer (Astra supplies changes by review message) | Shared records | `bench/versions.md`, `docs/decisions.md`, this file, anything under `interfaces/` |

- `interfaces/` changes need contract review by both sessions first.
- `bench/benchlog.md`: the session that ran a measurement writes its entry, on its own branch,
  with a unique run ID and an immutable evidence directory. Claude integrates reviewed entries.
- Branches: `feat/oak-d-pro` is the integration branch (Claude, `.worktrees/oak-d-pro`). Astra
  works on its own branch in its own worktree, created from the consolidated commit.
- Only one process ever opens the OAK. No second owner via depthai-ros or a standalone harness
  while the owner service runs. The unit keeps the name `garuda-oak-preview` through stereo
  integration; `:8096` preview stays compatible.

## 2. Mailbox

Directory (outside any checkout): `<development-checkout>/.local/oak-coordination/`
with `claude-to-astra/` and `astra-to-claude/`.

- One immutable new file per message, written atomically. Front matter: `id`, `from`, `to`,
  `kind` (`CONTRACT`, `READY_FOR_REVIEW`, `REVIEW`, `PI_REQUEST`, `PI_RELEASE`, `BLOCKED`, `ACK`),
  commit ID, affected paths, requested response.
- The recipient acknowledges by message ID. No acknowledgement means pending, never accepted.
- Each session reads its inbox at the start of a turn and before any edit, deployment or review.
- A file cannot wake an idle session. An unanswered message means "idle", not "rejected".
- Decisions live in committed documents; the mailbox is transport only.

## 3. Pi exclusion

- Lock = directory `…/.local/oak-coordination/pi.lock/` created with `mkdir` (atomic), containing
  `holder.json`: holder, session ID, purpose, run ID, acquisition time.
- A lock directory without complete holder metadata is BUSY. The lock is never taken over on a
  timer; release is explicit.
- While the lock is held, the other session does not touch the Pi at all — no SSH inventory, no
  `/stream`, `/api/events`, `/health` or SDK probe — unless it asked in the mailbox and the holder
  acknowledged first. Any monitoring is part of the holder's declared workload and is logged.
- Deployments, service restarts and measurements require the lock.
- Release message states: services left running, viewers, camera pipeline state,
  temperature and throttle flags, config and model hashes, cleanup still owed.
- CLAUDE.md rule 10 is unchanged: package installs and system changes are proposed to the owner.

## 4. `/pair` contract, version 1

Served by the owner process on loopback only. `/frame` (raw BGR, existing headers including
`X-Oak-Sequence`) is unchanged and stays as the rollback path. Public `/health` stays reachable
by the preview page with non-sensitive status; detailed diagnostics go on a separate loopback
endpoint.

### Envelope

```
4 bytes   unsigned big-endian N = length of JSON metadata
N bytes   UTF-8 JSON metadata
          RGB payload   (rgb.length bytes)
          depth payload (depth.length bytes; zero when absent)
```

- `Content-Type: application/vnd.garuda.oak-pair`
- `Content-Length` equals 4 + N + declared payload lengths.
- Metadata is capped at 16 KiB (protocol limit). With packed rows the payload sizes are fixed by
  the mode; the consumer rejects any other declared stride or length before reading a payload.
- All counts, lengths, sequences and timestamps are non-negative JSON integers. `epoch`,
  `calibration_id`, `depth_reference_frame`, clock ids and `boot_id` are non-empty strings;
  epochs are UUID-style so they need no query escaping. Device clock id is
  `oak-device-monotonic`. Native depth dimensions are positive when depth is valid.

### Metadata (required fields)

| Field | Meaning |
|---|---|
| `schema_version` | 1 |
| `epoch` | owner-process restart identity; a change invalidates all consumer pairing and tracking state |
| `pair_id` | assigned by the owner; distinct from the RGB sensor sequence |
| `rgb.seq`, `depth.seq` | each sensor output's own sequence number |
| `rgb.capture_device_ns`, `depth.capture_device_ns` | each output's own capture time in the OAK device clock domain — never one copied onto the other |
| `rgb.capture_host_ns`, `depth.capture_host_ns` | the host-monotonic mapping of the above |
| `clock_domain` | identities of both clocks |
| `rgb.width/height/stride/format/length` | `BGR888`, row-major. Stride is in bytes and in version 1 is exactly `width*3` (packed rows) |
| `depth.width/height/stride/format/length` | `Z16LE` millimetres, 0 = invalid; values that overflow uint16 are invalid, not clamped. Stride is exactly `width*2` in version 1 |
| `depth.native_width/native_height` | stereo resolution before alignment — upsampling adds no detail |
| `depth_reference_frame` | which camera's optical axis Z is measured along. **`unverified` until Astra records the SDK behaviour.** No consumer may assume the RGB axis before then |
| `calibration_id` | identifies the geometry/alignment transform in use |
| `depth_valid` | boolean |
| `depth_reason` | e.g. `ok`, `omitted_by_request`, `pair_wait_expired`, `skew_exceeded`, `stereo_unavailable` |
| `pair_skew_ns`, `pair_skew_limit_ns` | `pair_skew_ns` = depth capture minus RGB capture, signed, device-clock domain; the limit applies to its magnitude |

Left and right input capture times are kept in diagnostics/logs when the depth timestamp
represents one reference camera only.

### Request

`GET /pair?after=<pair_id>&epoch=<epoch>&depth=0|1`

- No `after`: returns the latest fresh pair.
- `after` + matching `epoch`: returns the next pair with `pair_id` greater than `after`, waiting
  up to the bounded wait; `204` if none arrives.
- `epoch` missing or different from the owner's current epoch: `after` is ignored and the latest
  fresh pair is returned with the current epoch. The consumer never waits on an ID from a
  previous run.
- `pair_id` increases monotonically within an epoch. It restarts with a new epoch.
- Latest wins: the owner keeps one current pair. A consumer slower than the camera skips
  pairs and always receives the newest; the cursor never replays skipped pairs.
- `depth` defaults to `1`.

### When depth is absent

`depth_valid=false`, `depth.length=0`, and `depth.seq`, `depth.capture_device_ns`,
`depth.capture_host_ns` and `pair_skew_ns` are JSON `null`. Never zero, never a copy of the RGB
values. Fields that describe configuration rather than a measurement stay populated when known:
`depth.width/height/stride/format`, `depth.native_width/native_height`, `calibration_id`,
`depth_reference_frame`, `pair_skew_limit_ns`.

### `clock_domain`

```json
"clock_domain": {
  "device": {"id": "oak-device-monotonic", "epoch": "<same as top-level epoch>"},
  "host":   {"id": "CLOCK_MONOTONIC", "boot_id": "<contents of /proc/sys/kernel/random/boot_id>"}
}
```

`*_device_ns` values are in `device`; `*_host_ns` values are in `host`. Device timestamps are
comparable only within one epoch; host timestamps only within one boot.

### Declared limits

The owner publishes these in `/health` under `limits`, and both sides' tests read the same
names. Values are the owner's configuration; none is fixed by this document.

| Name | Meaning |
|---|---|
| `pair_wait_ms` | longest the owner waits for a new pair before answering `204` |
| `depth_wait_ms` | longest the owner holds an RGB frame waiting for its depth before sending it RGB-only |
| `stale_age_ms` | acquisition older than this is answered `503`, never as a fresh pair |
| `rgb_queue` / `depth_queue` | bounded internal queue lengths; overwrites are counted in `/health` |
| `pair_skew_limit_ns` | as in the metadata |

### Behaviour

- A pair is immutable. The owner pairs RGB and depth on **device capture timestamps**, with a
  bounded internal queue and a bounded pairing wait. If the wait expires or the skew limit is
  exceeded, the RGB is returned with `depth_valid=false`, a reason and zero depth bytes. Depth
  problems never block or stop RGB detection. An old pair is never served as fresh.
- `?depth=0` omits depth bytes and reports `omitted_by_request` — not a valid measurement.
- Status codes: `200` pair or RGB-only as above; `204` no new frame within the bounded wait;
  `503` acquisition unavailable or stale; `400` invalid query.
- Skew limit: half an RGB frame period at the configured rate, as a **provisional bench
  maximum**. It is not a synchronization claim and not a navigation threshold. The final value
  is chosen from the measured skew distribution, motion-induced error and pair yield, all three
  recorded. No hardware frame-sync claim until verified for this exact configuration.
- Depth is aligned to the exact crop/resize used for the RGB payload. Alignment is tested at
  foreground/background boundaries, not assumed from factory intrinsics.

### Consumer rules (Claude's side)

- Field and UI label for depth: `z_m`, "stereo depth". Never "range". Body-frame or horizontal
  standoff is a separate derived output, computed from intrinsics/extrinsics and a stated target
  point.
- The valid-pixel fraction or dispersion score is shown as a quality score, not a probability.
- Tests cover: RGB-only with each `depth_reason`, `204`, `503`, epoch change, truncated or
  oversized envelope, metadata over the cap, payload length mismatch.

### What the mono side does with it (`GARUDA_MONO_OAK_PAIR=1`)

- Stereo depth is reported in an optional `stereo` block of `/api/state`, beside the existing
  `distance` block. It does not replace `distance`.
- **Owner directive 2026-11-12: the demo's SIMULATED follow uses stereo distance** whenever the
  `stereo` block is present (operator can untick it). Target position = z forward, z × pixel
  offset ÷ focal length sideways, origin at the camera, using the delivered 66.5275° horizontal
  field of view. A dropped measurement is bridged for at most 700 ms with the last depth and
  the current bearing; after that there is no position and the simulated vehicle brakes and
  holds. It never falls back to the model estimate or the schematic view. This is a browser
  simulation only: nothing here commands a vehicle, and z is still unvalidated and its axis
  unverified. The server-side follow in the portal is unchanged. The hold clock starts when a
  measurement is first seen (repeated snapshots do not renew it) and a held depth is shown as
  held. The operator's choice of source is latched: with stereo selected, a state without a
  stereo block means hold, never a switch to the floor or schematic projection; changing the
  source resets the simulated vehicle to home.
- **Projection is approximate.** Follow position and the 3-D view use the delivered horizontal
  field of view with a centred principal point and square pixels, not the delivered K
  (fx 975.651, fy 975.060, cx 642.416, cy 351.222). Proposed, not yet agreed: the producer
  publishes K (with width, height, reference frame and verification status) bound to
  `calibration_id`; a grid sampled every `step` pixels from pixel 0 uses K divided by `step`.
- **Exposure.** `/demo`, `/demo/cloud`, `/stereo_depth.jpg` and `/stereo_cloud.bin` serve the
  scene's colour and geometry to anyone who can reach `:8098`; the demo has no authentication.
  That is the existing exposure of the MJPEG stream, extended to depth. Keep it on the bench
  network. The depth picture and the 3-D view stop showing data (rather than freezing) when
  requests stall or the camera stops; the 3-D view's squares close the dot grid and do not
  represent recovered surfaces.
- **Main demo view:** a `STEREO x.xx m` readout and a "Depth map" button under the camera, with
  the depth picture as an inset on the live view.
- Only a locked target of class `person` is measured. Any other class is
  `unavailable / unsupported_target_class`: the estimator's region is a human torso.
- The block obeys the same freshness limit as `distance` (1500 ms). Older state is shown as
  `unavailable / stale_state`, so nothing is displayed as current while the owner is down.
- **Files.** With the flag off, `garuda_mono.py` imports none of `oak_pair.py`, `oak_distance.py`,
  `oak_stereo_state.py` (enforced by `test_mono_packaging.py`). Deploy the complete set
  anyway — `garuda_mono.py`, `mono_state.py`, `oak_capture.py`, `oak_pair.py`,
  `oak_distance.py`, `oak_stereo_state.py`, `oak_depth_view.py` (seven with the depth view) — so
  turning the flag on never meets a missing file.
- **Depth map for looking at:** `GET /stereo_depth.jpg` on `:8098` returns a depth frame,
  downsampled and colourised (red near, blue far, black no depth, 0–6 m). It is a picture, not a
  measurement. It shows the pair the current measurement was made on, with that measurement's
  region outlined (`X-Stereo-Outline: matched`); if that pair is no longer stored it shows the
  newest depth with **no** outline (`none`) — an outline is never drawn over a different pair.
  Depth older than 1500 ms is not served (`503`), so a stopped camera cannot leave a
  live-looking picture. While it is being requested, depth bytes are fetched even with no
  target locked. `404` when the flag is off.
- **Restart behaviour, as implemented:** when the owner restarts, tracks, lock, gestures and
  cached depth are cleared. The lock is not pinned afterwards, so the existing automatic
  class selection may lock a target again without anyone clicking. It is a reset and
  recovery, not "wait for the operator to reselect". Whether it should wait is an owner
  decision (open).

### `/health` additions

Enabled streams; last acquisition time and age per stream; actual rates; queue overwrites;
sensor sequence gaps (kept distinct from proven lost samples); pairing misses; restart epoch;
per-stream error state. A stopped IMU must not leave the aggregate status green.

## 5. Order of work (named stages)

1. **Consolidation** — Claude; Astra reviews. *(done when Astra's REVIEW is in)*
2. **Offline IMU analysis** and **estimator tests** — Claude, no Pi.
3. **Stereo and pairing in the owner process** — Astra, holds the Pi lock. In parallel Claude
   builds **distance consumption** against fixtures. Both commits reviewed, then a pinned pair
   of revisions is integrated.
4. **Integrated deploy, soak, tape-measure and walk-test** — one lock holder; owner supplies
   tape positions, walking and unplug/replug.
5. **QGC video link** (H.265, then H.264) — Astra. Parser/payloader plugins resolved
   individually; exact packages proposed to the owner before install; no software encoders.
6. **Final concurrent test** — one operator, the agreed logger, both sessions review the
   evidence. **Stop and report.**

## 6. Deferred by owner decision (2026-11-12)

PTP installation, Docker/ROS installation and any change of OAK device ownership wait until
after the final concurrent test review. Timestamp semantics and future topic/frame names may be
frozen on paper meanwhile. This changes the original work order; its time-base and topic-contract
steps stay **open**. No VIO, EKF2 or cross-machine timing claim is made before a time reference
exists.

## 7. Cooling and what counts as evidence

Passive heatsink per repository records; whether a cooler has since been fitted is unconfirmed
by the owner. Every run records actual temperature and throttle flags. Cold-start and
clean-interval results are valid evidence. Active limiting invalidates a sustained-performance
pass; the throttled trace is kept as failure evidence. The cooled soak gate stays open until the
actual setup passes it.

## 2026-11-12 owner-directed completion handoff

Owner requested Astra take over from Fable and finish the original bring-up scope.
Astra consolidated integration commit `1b5ace3` with the producer/evidence branch in
`b7f8a2a`, preserving both histories and bench entries. Prior file ownership is superseded
by sole-writer Astra ownership after handoff; the existing Pi lock still requires explicit
release to prevent a late cleanup racing the next measurement. No real-flight work is added.

### Additive pair geometry v1.1 / cloud GCL2

`/pair` envelope schema remains 1. Optional `geometry` contains `schema_version:1`,
`epoch`, `calibration_id`, `width`, `height`, `fx`, `fy`, `cx`, `cy`,
`distortion:"none"`, `source:"sdk-transformation-metadata"`,
`reference_frame:"unverified"`, `verified:false`. Epoch/calibration/dimensions must
match the enclosing pair; finite positive focal lengths and in-image principal point
are required. Missing geometry is explicitly the legacy approximate projection; invalid
present metadata rejects the pair, never reuses a previous owner's geometry.
Verification flag refers to physical geometry/axis, not whether SDK metadata was read.

Cloud GCL2: four ASCII magic bytes, unsigned little-endian uint32 JSON size (max 16 KiB),
JSON `{geometry,step,width,height}`, then packed little-endian uint16 depth mm and BGR8.
`step` is 2, 4 or 8; grid dimensions are ceil(source dimensions / step). Pixel sampling
starts at zero, so grid K is delivered K / step. Geometry and pixels share one pair.
GCL1 remains readable for old producers; no latest-health mixing. Neither format proves
optical-axis semantics, physical accuracy, unseen surfaces or mapping.

### Host-stereo review qualification (2026-11-12)

Host normal `/pair` is an independent RGB-only publisher while geometry/reliability
remain unqualified. `/pair-preview` is an explicit loopback preview channel carrying
completed paired depth; IDs belong to their respective channel, not a common sequence.
Both share camera epoch/calibration. `measurement_qualified:false` prevents normal
consumption; `preview_only:true` marks preview payloads. Calibration explicitly opts
in. `depth_valid` on a preview means payload present, not reliable metric depth.
`depth_nonzero_fraction` is image coverage, not confidence. Do not use these previews
for follow control. Source hashes and runtime versions are in health; per-frame traces
are available via `/timing`. See OAK_HOST_STEREO.md for the bounded-worker contract.
