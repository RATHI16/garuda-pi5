# One reviewed release for the owner, Chennai and CES teams

Status: proposal, not a published release. No new tag or push is implied here.

The development repository is `garuda-sdk-bench`. The colleague-facing `garuda-pi5`
repository is a separate initial import (`f19835a` when last inspected), not a remote
branch of the current bench history. Do not tell a colleague that pulling it delivers
these changes until an import/update has actually been reviewed and pushed.

## Release candidate

- Keep `integ/unified-ui-camera` as the integration candidate while the paired reviews
  and on-device tests run. Record its full immutable commit ID in the release manifest.
- Proposed tag after those gates: `bench-camera-2026-11-15-rc1`; adjust date to the actual
  release. Do not create a final/product tag from test counts alone.
- Prepare a clean export/import for `garuda-pi5` with its own history preserved. Review the
  complete tree diff and run the same tests from the export. Record source bench commit
  and resulting team-repository commit together. No force push or history replacement.
- Owner chooses/authorises the destination push. A local commit is not team delivery.

## Per-site configuration, not divergent source code

| Site | Profile | Default use | Required first check |
|---|---|---|---|
| Owner bench | OAK, fixed mount | Browser/PX4 simulation with explicitly accepted unvalidated stereo | Original fixed demo still works; shadow mode only when chosen |
| US CES | CM3, vehicle mount on a stand | Tracking + unsent command panel, no flight link | CM3 capture, models, crowd selection, show-duration cooling and restart |
| Chennai | CM3 first, OAK later, vehicle mount | Shadow carry-around first | Sign/age/identity-loss checks, power/cooling, then separately reviewed receive-only FC link |

Use `UNIFIED_CAMERA_PROFILES.md` for startup settings and `ON_VEHICLE_BRINGUP.md` for the
stages. Keep device addresses, model paths and service drop-ins outside tracked shared
source. Record each Pi's commit, OS/kernel, Hailo firmware/runtime, camera model, module
hashes and model hashes. Do not hot-plug CSI cables.

This bench currently has no working CSI/USB camera available (Claude's 2026-11-15 inventory).
The teams previously used CM3 successfully, but the new release still needs a CM3 first-run
at their site. Keep the previous known-working release and a tested rollback path.

## Export exclusions and licensing

Never export `.local/`, `.runtime/`, private recordings or faces, SSH keys/credentials,
user-specific configuration, Pi lock/mailbox contents, temporary virtualenvs, downloaded
model binaries or unreviewed datasets. Do not silently redistribute model weights or
OAK firmware. Include model-fetch instructions and licence notices, with AGPL evaluation
components explicitly labelled not-product-cleared; a bench export is not product clearance.

Release notes must separate: unit/integration tests; Pi runtime checks; CM3 first-run;
static CES endurance; carry-around evidence; simulated closed-loop results; calibration;
and real-FC checks. Unperformed stages remain open. No release note may imply real-flight
approval, calibrated mono depth, measured stereo accuracy or multi-person re-ID validation.
