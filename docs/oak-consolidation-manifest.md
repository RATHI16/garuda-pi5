# OAK consolidation manifest (2026-11-12)

Copy-only consolidation onto `feat/oak-d-pro`. Sources were left untouched. SHA256 computed on
the PC from the source file and the copied file (they must match). "Deployed path" is where the
Codex session's benchlog entries say the file was installed on the Pi; a Pi-side md5 check at
~12:45 IST 2026-11-12 matched for the three files marked †. Other deployed copies were not re-hashed. ‡ = path supplied by the deploying session (astra-0002) from memory of the deployment, not rechecked live.

Source roots: **M** = main checkout (`master` @ 2322c1b, uncommitted), **P** = `.worktrees/px4-gazebo-demo` (@ 941b80d, uncommitted).

| File (this branch) | Source | SHA256 | Match | Deployed path on Pi |
|---|---|---|---|---|
| `hosts/pi5/oak/preview.py` | M | `2533ca6baa915113e3b8eb1ac70ccf335c7d33501592205fd57cdb0f9da9a8c4` | yes | `/home/garuda/oak_preview.py` † |
| `hosts/pi5/oak/first_light.py` | M | `8a3040f16d4f34d9db6c9353bf765ae01081f1fd5001c33d2f1f286cda5bfe72` | yes | `/home/garuda/first_light_oak.py` |
| `hosts/pi5/oak/garuda-oak-preview.service` | M | `85bc3b99f75c67606e939b4b20e12fd1d48a028fe4fa40dafbf17a40bd843eff` | yes | `/etc/systemd/system/garuda-oak-preview.service` ‡ |
| `hosts/pi5/oak/oak-camera.conf` | M | `2ad0c3c462bc1279baa28b6fc9df3ededad4d3301a6a8e64a712f7ceed800cb5` | yes | `/etc/systemd/system/garuda-mono.service.d/oak-camera.conf` |
| `hosts/pi5/oak/80-garuda-oak.rules` | M | `fe23c5eb48282c4a614ba61c49fb778029b48871b7c65a953078d031836f6e45` | yes | `/etc/udev/rules.d/80-garuda-oak.rules` ‡ |
| `hosts/pi5/oak/README.md` | M | `af3f6bbdbbab04abb7f0916eabda08854fc2e9111236be4a481922b2485681aa` | yes | — |
| `hosts/pi5/garuda_mono.py` | P | `555bae1d210249d7ea3a156e8a9dae41947a8a91e388df94ed58a88935b46f1c` | yes | `/home/garuda/garuda-sdk-deploy/hosts/pi5/garuda_mono.py` † |
| `hosts/pi5/oak_capture.py` | P | `bf5c4c8ae3ce4c36325b39657ed77d3a2a57c657c54ef174d58ebf20659d07db` | yes | `/home/garuda/garuda-sdk-deploy/hosts/pi5/oak_capture.py` † |
| `hosts/pi5/test_oak_capture.py` | P | `18f0f3dab1e8a262731d399817ce57eb8853ea2660be00cfcdb74af0659e1c14` | yes | — |
| `yocto/meta-garuda/recipes-sensors/depthai/depthai-core_3.12.1.bb` | M | `deb02c27ef6ae5efc55f56ce9370bbb157c73573268c0afd2675159386c2078d` | yes | — |
| `yocto/meta-garuda/recipes-sensors/depthai/python3-numpy_2.5.4.bb` | M | `218545024151485dec16e4b659d761d113af0df0a8cf5e938db48b06568ac906` | yes | — |
| `docs/research/2026-11-12-oak-bringup-contract.md` | M | `48f752637a72225053695c52ee310ac7f1c1ae13feadd36dd6ebe9f784d60758` | yes | — |
| `bench/results/2026-11-12-oak-precheck/after-first-light.txt` | M | `953c5149230053a6547d9d7d94f80fa45aecde45838218a46bea1acf47caf717` | yes | — |
| `bench/results/2026-11-12-oak-precheck/analysis.json` | M | `4b7f3112d4796dbbe754fb90c35a1588655665150b87a28629064188dff3f4fd` | yes | — |
| `bench/results/2026-11-12-oak-precheck/calibration.json` | M | `35e0993ac33cc4757af92cbf29b19cad49d0c3420e741079862889f9b0e6807a` | yes | — |
| `bench/results/2026-11-12-oak-precheck/first-light-01.log` | M | `decfc7c3aa5024a4d8b1f917ebc987fa848d551c459d745bee7d594fdf35cf53` | yes | — |
| `bench/results/2026-11-12-oak-precheck/first-light-summary.json` | M | `fdb0b433747468fc44dcd1d2d5e96e753681d1b3a512df9c3e4d810431512a12` | yes | — |
| `bench/results/2026-11-12-oak-precheck/installed-licenses.json` | M | `1f42c8000c8996fd48faf62b83932a64bcb5981a1dc9f0f794c2816cc3fd8a67` | yes | — |
| `bench/results/2026-11-12-oak-precheck/license-sources.json` | M | `e2e945bb56db3949dd9dd5d1a1a1b4c42c36c927cd779c8fb94cf45e5f4e7b90` | yes | — |
| `bench/results/2026-11-12-oak-precheck/oak-detection-state.json` | M | `8303714be542433c85c12115260cbcc786dacdf5256caa639ea2511a9a25622a` | yes | — |
| `bench/results/2026-11-12-oak-precheck/pi-environment.txt` | M | `3db145dfa2484f65b7aa3ac8fc1c0623eed218b274a69c0fef28404604bd5517` | yes | — |
| `bench/results/2026-11-12-oak-precheck/precheck.json` | M | `b417ad29f0f663488f7b51c8653361ee34aed4057ef4b90cfab4ca214a194f37` | yes | — |
| `bench/results/2026-11-12-oak-precheck/preview-health.json` | M | `9226d26c8768da36fc673caa32624a4e33aab7459ed3778175b1cf757d4dca6b` | yes | — |
| `bench/results/2026-11-12-oak-precheck/samples.jsonl.gz` | M | `df60dede8777241c2707b75268cfa8397419c91adfcc9082e2fb56f7280fd15e` | yes | — |

## Not copied, on purpose

- `README.md` edit and `docs/research/2026-11-monocular-vio-hailo-drone-navigation.md` in M: predate the OAK work; left for the owner to place.
- `.local/`, `acceleras.log`, `hailo_sdk.client.log`, `__pycache__/` in M.
- First-frame camera images: they remain on the Pi only.

## Merged rather than copied

- `bench/benchlog.md`: the four OAK entries added in M were appended verbatim. P's single added entry was byte-identical to M's fourth and appears once.
- `bench/versions.md`: the two OAK entries added in M were appended verbatim.
