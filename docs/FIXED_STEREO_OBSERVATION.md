# Fixed-camera stereo observation — `garuda.fixed-stereo-observation.1`

Status: proposed interface (astra-0061), implemented by `hosts/pc/fixed_stereo_observation.py`
(`decode(snapshot)`), not yet consumed by any controller. Simulation and bench use only.

One detector snapshot (the JSON of `GET /api/state` or one `/api/events` message from the
Pi detector running with `GARUDA_MONO_OAK_BOX=1`) goes in; one observation comes out. The
decoder is pure: no I/O, no clock, no memory of earlier snapshots.

## Fields

| Field | Meaning |
|---|---|
| `schema`, `source` | `garuda.fixed-stereo-observation.1`, `fixed_stereo` |
| `source_instance` | detector process identity; changes when the detector restarts |
| `frame_epoch`, `frame_seq` | detector frame identity (`oak-rgb:<camera run>:<detector generation>`, counter) |
| `target_id`, `target_class`, `coasting`, `confidence` | the camera's selected track in this frame |
| `camera_capture_host_ns` | capture time on the **Pi's** monotonic clock. Provenance only; not comparable with a PC clock |
| `age_ms` | the producer's age of this frame when the snapshot was built. The reader adds transport and residence time on its own clock |
| `pair` | `{epoch, rgb_seq}` of the camera frame the depth was measured on |
| `calibration_id`, `estimator_config`, `roi` | geometry identity, estimator thresholds identity, measured region `[x0,y0,x1,y1]` in image pixels |
| `status`, `reason` | the producer's own result (`ok`, `invalid`, `ambiguous`, `unavailable` with its reason), or `unavailable` with `adapter:<cause>` when a precondition below fails |
| `optical_point_m` | `[right, down, forward]` in metres in the undistorted colour camera's optical frame, or `null`. `forward` is optical-axis depth, not radial distance. The point is the centre of the torso region at the measured depth: not the person's feet, root or nearest surface |
| `measurement_qualified`, `preview_only` | copied from the producer; anything but a literal boolean reads as `false` / `true`. Today always `false` / `true` |

## When there is a point

All of: producer `status == "ok"`; depth finite and inside 0.3-30 m; a selected track that
is in the frame, is a person, is not coasting and has confidence 0.5-1; `frame.capture_pair`
equal to `stereo.pair` (depth from exactly this image); geometry present, undistorted, with
the block's `calibration_id`, the pair's epoch, the image's size, positive finite focal
lengths and a principal point inside the image; a region inside the image.

Otherwise `optical_point_m` is `null` and `reason` says why. The decoder never raises,
never reuses an earlier value, never substitutes another range, and never changes the
qualification flags.

## What the decoder does not do

- It does not decide whether an unqualified preview may be used. The consumer's explicit,
  default-off simulation switch does (`--accept-preview-stereo-in-simulation`, owned by
  the controller side).
- It applies no freshness limit, selection latch, or continuity rule; those belong to the
  consumer, which also owns its own ingress timestamp.
- It does not place the camera in any world. World or NED coordinates need a mount
  transform, which is separate and, for now, assumed rather than measured.

## Producer-side facts a consumer should know

- `frame.capture_pair` is published in box mode as of this change; earlier builds omitted it.
- After a camera restart the detector clears its pinned selection, then follows an
  unpinned default target (the best track of the wanted class) until someone selects.
  That is not operator consent.
- A track unmatched for more than `GARUDA_MONO_MAXLOST_S` of capture time (default
  25 frames at 15 fps = 1.67 s) is not re-attached under its old ID. Inside that window a
  coasting track can return with the same ID; `coasting` is true while it is unmatched.
- The distance is unvalidated: one tape comparison read about 28 % long at 1.1 m.
