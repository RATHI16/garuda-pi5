# Full setup from zero: Garuda booth demo (Pi 5 + Hailo-10H + PX4 yaw tracking)

Follow in order. `[Pi]` = SSH/terminal on the Pi, `[Win]` = Windows PowerShell on the laptop,
`[QGC]` = QGroundControl > Analyze Tools > MAVLink Console. Each part ends with a check: do not go on until it passes.
Status: written from the working bench; a complete fresh install from this guide has not been repeated yet.
If a step fails, note the exact message.

## 0. What you need
| Item | Notes |
|---|---|
| Raspberry Pi 5 (8 GB), official 27 W PSU for the bench | on the drone: a 5 V / 5 A supply; `vcgencmd get_throttled` must be `0x0` |
| Raspberry Pi AI HAT+ 2 (Hailo-10H) | PCIe ribbon + stacking header |
| Camera: Camera Module 3 **or** Arducam AR0234 (CSI), or a USB camera | CSI only plugged with power OFF |
| microSD 64 GB+ | Raspberry Pi OS Trixie 64-bit |
| PX4 flight controller with **Ethernet** MAVLink, optical flow + downward rangefinder (e.g. Holybro H-Flow over DroneCAN) | indoor position/altitude hold needs both |
| Ethernet cable FC <-> Pi | |
| Windows laptop with 5 GHz WiFi, QGroundControl | VPN must be OFF when talking to the Pi |
| Files not in git: HailoRT 5.3.0 (3 files), `yolo26m.hef` (+ optional models) | see parts B and C |

## A. Raspberry Pi OS
1. Raspberry Pi Imager: **Raspberry Pi OS (64-bit) Trixie**. Settings: hostname `garuda`, **user `garuda`** (the code uses
   `/home/garuda/...`), enable SSH, your WiFi for the first setup (needs internet).
2. `[Pi]`
   ```bash
   sudo apt-get update
   sudo apt-get install -y git curl python3-numpy python3-opencv python3-venv python3-pip rpicam-apps v4l-utils pciutils tmux
   sudo usermod -aG video garuda
   sudo reboot
   ```
   Do **not** run `apt upgrade` later without a reason: kernel updates can break the Hailo driver.
3. Check: `lspci | grep -i hailo` shows the Hailo device; `rpicam-hello --list-cameras` lists the camera
   (AR0234: do part D first).

## B. HailoRT 5.3.0 (must match the models)
Get from the Hailo Developer Zone (login), for Hailo-10H / ARM64 / Python 3.13:
`hailort-pcie-driver_5.3.0_all.deb`, `hailort_5.3.0_arm64.deb`, `hailort-5.3.0-cp313-cp313-linux_aarch64.whl`.
Do not use `hailo-h10-all` from apt for this demo unless `apt-cache policy hailo-h10-all` shows 5.3.0, and never mix
the h10 apt packages with these files. 5.4.0 files do not run 5.3.0 models.

1. `[Win]` `scp hailort-pcie-driver_5.3.0_all.deb hailort_5.3.0_arm64.deb hailort-5.3.0-cp313-cp313-linux_aarch64.whl garuda@<pi>:~`
2. `[Pi]`
   ```bash
   cd ~
   sudo apt-get install -y ./hailort-pcie-driver_5.3.0_all.deb ./hailort_5.3.0_arm64.deb
   ```
3. **Only if the driver build fails** with `implicit declaration of function 'del_timer'` / `del_timer_sync`
   (new kernels renamed these functions), patch the driver source and rebuild:
   ```bash
   dkms status                                              # e.g. hailo1x_pci/5.3.0: added
   sudo grep -rl "del_timer" /usr/src/hailo1x_pci-5.3.0/    # lists the files, e.g. .../linux/vdma/monitor.c
   sudo sed -i 's/del_timer_sync/timer_delete_sync/g; s/\bdel_timer\b/timer_delete/g' <each file listed>
   sudo dkms build hailo1x_pci/5.3.0 && sudo dkms install hailo1x_pci/5.3.0
   dkms status                                              # must show: installed
   ```
4. `[Pi]` `pip3 install --break-system-packages --no-deps ~/hailort-5.3.0-cp313-cp313-linux_aarch64.whl`, then `sudo reboot`
5. Check:
   ```bash
   hailortcli fw-control identify          # HAILO10H, firmware 5.3.0
   python3 -c "import cv2, numpy, hailo_platform; print('python OK')"
   ```
   Note: on the Hailo-10H use `hailortcli run2 ...` (not `run`) for model tests.

## C. Garuda code + models
1. `[Pi]`
   ```bash
   git clone -b booth-working https://github.com/RATHI16/garuda-pi5.git ~/garuda-sdk-deploy
   mkdir -p ~/garuda-sdk-deploy/models
   ```
2. Copy the model files into `~/garuda-sdk-deploy/models/` (from a working Pi or your own Hailo-10H / 5.3.0 builds):
   | File | Needed |
   |---|---|
   | `yolo26m.hef` | **required** |
   | `yolov8m_pose_h10.hef` | only for pose / gestures |
   | `osnet_x1_0.hef` | only if you turn re-ID on |
   | `faces/yunet.onnx`, `faces/sface.onnx` | only for face matching: `cd ~/garuda-sdk-deploy && python3 models/fetch_faces.py` |
3. `[Pi]` `pip3 install --break-system-packages pymavlink` (needed for the FC bridge)
4. Check: `hailortcli run2 set-net ~/garuda-sdk-deploy/models/yolo26m.hef` runs without error (Ctrl+C to stop).

## D. Camera
- Camera Module 3: works as is (`GARUDA_MONO_CAM=csi`).
- Arducam AR0234: follow [`ARDUCAM_AR0234.md`](ARDUCAM_AR0234.md) (packages from Arducam + 2 `config.txt` lines + reboot).
- USB camera: in `~/booth/garuda-mono.env` (after part E) set `GARUDA_MONO_CAM=/dev/videoN` (`v4l2-ctl --list-devices`).
- Check: `rpicam-hello --list-cameras` lists the camera.

## E. Booth setup on the Pi (camera service, hotspot, FC Ethernet link)
1. `[Pi]`
   ```bash
   cp -r ~/garuda-sdk-deploy/booth ~/booth
   GARUDA_AP_PSK=<choose-a-wifi-password-8+chars> bash ~/booth/booth_setup_once.sh
   ```
   It installs the `garuda-mono` service (starts at boot, restarts itself), creates the **GarudaBench** hotspot
   (`10.43.0.1`) and the FC Ethernet profile `eth-link` (Pi `192.168.100.12/24`), copies the bridge and checks tools.
   Check: `RESULT: SETUP DONE`, and `OK camera service answering (200)`.
2. Laptop: join WiFi **GarudaBench** (VPN off), open `http://10.43.0.1:8098/demo`: live picture with boxes on people.
3. Smooth page: move the hotspot to 5 GHz once: `[Pi]` `bash ~/booth/hotspot_5ghz.sh`, reconnect the laptop to
   GarudaBench at once; after 6 min `cat ~/hotspot_5ghz.log` must say `KEPT 5 GHz`
   (set `COUNTRY` in the script first if you are not in India).
   Laptop: Settings > Power mode **Best performance**; Device Manager > WiFi adapter > Power Management > untick
   "Allow the computer to turn off this device".

## F. Flight controller (PX4)
1. Firmware with Ethernet MAVLink (our board: Microchip SAMV71-based FC, PX4 1.17-alpha; FC address `192.168.100.3`).
   Other addresses: change `FC=` in `booth/booth_fly.sh`, the `-t` address in `fc_sdcard/etc/extras.txt`, and the
   Pi address in `booth_setup_once.sh`.
2. Onboard MAVLink link at every boot: on the FC SD card create `etc/extras.txt`
   (copy `booth/fc_sdcard/etc/extras.txt`, or in `[QGC]`):
   ```
   mkdir /fs/microsd/etc
   echo "mavlink stop -u 14550" > /fs/microsd/etc/extras.txt
   echo "mavlink start -u 14540 -o 14540 -t 192.168.100.12 -m onboard -r 20000" >> /fs/microsd/etc/extras.txt
   cat /fs/microsd/etc/extras.txt
   ```
   (`mavlink stop -u 14550`: our firmware allows only one Ethernet MAVLink link. If yours has no 14550 Ethernet link,
   the line does nothing.) Reboot the FC. Check `[QGC]` `mavlink status`: `UDP (14540, remote port: 14540)`, mode `Onboard`.
3. Parameters used on our drone. Write down your current values before changing any:
   | Parameter | Value | Why |
   |---|---|---|
   | `EKF2_HGT_REF` | 2 | height from the rangefinder (indoor) |
   | `EKF2_RNG_CTRL` | 2 | rangefinder fused |
   | `EKF2_BARO_CTRL` | 1 | barometer as backup |
   | `EKF2_OF_CTRL` | 1 | optical flow for position |
   | `EKF2_GPS_CTRL` | 0 | indoor, no GPS |
   | `EKF2_MIN_RNG` | your rangefinder reading on the floor (ours 0.13) | assumed range while landed |
   | `UAVCAN_ENABLE` / `UAVCAN_SUB_FLOW` / `UAVCAN_SUB_RNG` | 2 / 1 / 1 | H-Flow over DroneCAN |
   | `COM_RC_OVERRIDE` | 3 | stick movement takes over from OFFBOARD |
   | `COM_OF_LOSS_T` | 1.0 | offboard loss detected after 1 s |
   | `COM_OBL_RC_ACT` | 0 | offboard loss -> Position mode |
   RC: a mode switch with **Position** and **Offboard**, and a kill switch. Calibrate sensors, then **reboot the FC**.
4. The bridge finds the FC system ID itself (`booth_fly.sh`).

## G. Laptop one-click launcher
1. Copy `booth/windows/` to the laptop (e.g. `Desktop\GARUDA_DEMO\`).
2. Once: right-click `garuda_ssh_key_once.ps1` > Run with PowerShell (Pi password one last time) -> `KEY OK`.
3. Every demo: double-click `GARUDA_DEMO.bat`: 1 dry run, 2 live, 3 stop, 4 watch.
   Logs: `[Win]` `scp -r garuda@10.43.0.1:booth_yaw_logs $HOME\Desktop\garuda_logs\`

## H. Preflight check (every flight)
1. Drone on the floor, powered for at least 30 s without being moved (after any calibration: reboot the FC first).
2. `[QGC]` `listener estimator_aid_src_rng_hgt` must show **`fused: True`** (rangefinder used for height).
   `False` -> reboot the FC, keep it still, check again. Do not fly OFFBOARD without it: height will wander.
3. `GARUDA_DEMO.bat` > 1 dry run: every check `OK`, `[PRESTREAM]`; click the person on the page.
4. Props-off checks, then the flight: [`FLY_STEPS.txt`](FLY_STEPS.txt) and [`BENCH_STEPS.txt`](BENCH_STEPS.txt).

## I. What the demo does
Pilot takes off in Position and switches to OFFBOARD: the drone holds its position and height and turns to face the
selected person (max 0.25 rad/s, no yaw limit by default). Person lost: it keeps facing where it last saw them.
FC link lost / disarm / bridge freeze in OFFBOARD: the bridge sends nothing, PX4 switches to Position.
Switching the RC to Position ends the demo at any time. Fly tethered first, pilot ready on mode switch and kill switch.
