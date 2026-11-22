# Booth branch (`booth-working`): Garuda Pi 5 + Hailo-10H with PX4 yaw tracking

This branch = Bhanu's [`garuda-pi5`](https://github.com/bhanuprakashjh/garuda-pi5) commit `6103e65` (unchanged on `main`)
**plus**:

1. `hosts/pi5/garuda_mono.py`: optional low-bandwidth browser stream. Three settings, all off unless set:
   `GARUDA_MONO_STREAM_W` (picture width, e.g. 640), `GARUDA_MONO_STREAM_Q` (JPEG quality, e.g. 60),
   `GARUDA_MONO_STREAM_NEW_ONLY=1` (send only new pictures). Detection, tracking and the drone command
   still use the full camera frame. Diff: `git diff main booth-working -- hosts/pi5/garuda_mono.py`.
2. `booth/`: the booth yaw bridge to a real PX4 FC (position + altitude hold, heading follows the person),
   Pi setup / fly scripts, camera service settings, Windows one-click launcher, FC SD-card file,
   bench and flight steps. See [`booth/README.md`](booth/README.md).

Bhanu's own `fc_bridge.py` stays receive-only. Licence: BSD 3-Clause (see `LICENSE`), copyright the Garuda SDK project.

**New setup from zero: follow [`booth/SETUP_NEW_PI.md`](booth/SETUP_NEW_PI.md)** (Pi OS, HailoRT 5.3.0, camera, booth setup, FC parameters, laptop, preflight).

## What is NOT in git (needed on the Pi)
| Item | Where it goes | Notes |
|---|---|---|
| HailoRT **5.3.0** driver + runtime + Python wheel | installed on the Pi | from the Hailo Developer Zone (login); 5.4.0 HEFs do not run on 5.3.0 |
| `yolo26m.hef` (Hailo-10H, HailoRT 5.3.0) | `~/garuda-sdk-deploy/models/` | **required** at startup |
| `yolov8m_pose_h10.hef` | `~/garuda-sdk-deploy/models/` | optional: only for pose / gesture mode |
| `osnet_x1_0.hef` | `~/garuda-sdk-deploy/models/` | optional: re-ID (`GARUDA_MONO_REID_HEF`); off in the booth settings |
| `faces/yunet.onnx`, `faces/sface.onnx` | `~/garuda-sdk-deploy/models/faces/` | optional: face matching; `python3 models/fetch_faces.py` |
| Arducam AR0234 only: Arducam libcamera + rpicam-apps packages, `config.txt` overlay | Pi system | see [`booth/ARDUCAM_AR0234.md`](booth/ARDUCAM_AR0234.md) (packages from Arducam, not in git) |
| `pymavlink`, `tmux` | Pi system | `pip install pymavlink` (or the offline wheel), `sudo apt install tmux` |

Model binaries are deliberately not committed (see Bhanu's `.gitignore` / `models/LICENCES.md`; YOLO26 is not
product-cleared, OSNet is evaluation-only). Copy them from a working Pi or obtain them yourself.

## Install on a Pi (user `garuda`, Raspberry Pi OS, HailoRT 5.3.0 already working)
```bash
git clone -b booth-working https://github.com/RATHI16/garuda-pi5.git ~/garuda-sdk-deploy
# copy the model files into ~/garuda-sdk-deploy/models/ (see table above)
cp -r ~/garuda-sdk-deploy/booth ~/booth
bash ~/booth/booth_setup_once.sh          # camera service, hotspot + Ethernet at boot, bridge copy
```
If no WiFi hotspot exists yet: `GARUDA_AP_PSK=<your-password> bash ~/booth/booth_setup_once.sh`.
The setup also creates the FC Ethernet profile `eth-link` (Pi `192.168.100.12/24`) if missing.
For a lag-free `/demo` page move the hotspot to 5 GHz once: `bash ~/booth/hotspot_5ghz.sh` (auto-reverts to
2.4 GHz after 5 min if the laptop does not reconnect; set `COUNTRY` in the script).
Camera page: `http://<pi>:8098/demo`. Then follow `booth/FLY_STEPS.txt` (dry run, props-off checks, tethered flight).

## Adapt to your setup
FC / Pi Ethernet addresses (`192.168.100.3` / `.12`) in `booth/booth_fly.sh` and `booth/fc_sdcard/etc/extras.txt`;
hotspot address `10.43.0.1` in `booth/windows/garuda_demo.ps1`; camera in `booth/garuda-mono.env`
(`GARUDA_MONO_CAM=csi` or `/dev/videoN`). Paths assume user `garuda` and `~/garuda-sdk-deploy`
(hard-coded in Bhanu's code).
