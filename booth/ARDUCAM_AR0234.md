# Arducam AR0234 (B0353, 2.3 MP global shutter, colour) on the Pi 5: offline install

Used instead of the Camera Module 3 for the booth drone. Works with Hailo-10H / HailoRT 5.3.0 untouched
(the camera stack and the Hailo use different paths: CSI/libcamera vs PCIe).

**Why offline / by hand:** Arducam's `install_pivariety_pkgs.sh` downloads from the internet and runs `apt update`;
it fails without WiFi and is the script that hung on Bhanu's Trixie Pi (`docs/decisions.md`, 2026-08-01).
Here the **same Arducam packages** are installed by hand with `dpkg`, after a backup, with a tested undo.

**Packages are NOT in this repo** (Arducam binaries, tied to one Pi OS/libcamera build). Get them from Arducam
(their AR0234 / Pivariety documentation and install script, which names the files) on a PC with internet,
and copy them to the Pi. Also keep the **stock** Raspberry Pi packages of the same version for the undo.

Versions that worked (Raspberry Pi OS Trixie, 2026-11-04):

| Set | libcamera | rpicam-apps |
|---|---|---|
| Arducam (`arducam_debs/`, 10 files) | `0.7.2+rpt20261010-2` | `1.14.0-2` |
| Stock, for undo (`stock_rollback/`, 12 files) | `0.7.2+rpt20261009-1` | `1.14.0-1` |

The Arducam build is the same libcamera version plus Pivariety support, so the dependencies already on the Pi
were enough. If your stock version differs, get the matching Arducam build.

`[PC]` = laptop terminal, `[Pi]` = SSH on the Pi.

## 1. Backup (do not skip)
```bash
pkill -f garuda_mono.py; pkill rpicam; sudo systemctl stop garuda-mono 2>/dev/null
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.cm3-working
dpkg -l | grep -E "libcamera|rpicam|libpisp" > ~/camera-before.txt
cat ~/camera-before.txt          # note the libcamera0.7 version
```

## 2. Copy the packages
```
[PC] scp -r arducam_debs stock_rollback garuda@<pi>:~
[Pi] ls ~/arducam_debs ~/stock_rollback
```

## 3. Install the Arducam packages (offline)
```bash
cd ~/arducam_debs
sudo dpkg -i libcamera0.7_*.deb libcamera-ipa_*.deb libcamera-dev_*.deb
sudo dpkg -i librpicam-app1_*.deb rpicam-apps-core_*.deb rpicam-apps-encoder_*.deb rpicam-apps-preview_*.deb rpicam-apps-opencv-postprocess_*.deb rpicam-apps_*.deb librpicam-app-dev_*.deb
dpkg -l | grep -E "libcamera0.7|rpicam-apps-core"     # OK = ...-2 versions (Arducam)
```
| Error | Fix |
|---|---|
| `dependency problems ... X is not installed` | `sudo apt-get install -f` (needs internet); if it wants downloads and you have none: undo (step 8) |
| `librpicam-app-dev ... depends on ...` | harmless (dev files): `sudo dpkg -r librpicam-app-dev` |
| `python3-libcamera depends on libcamera0.7 (>= ...)` warning | ignore |
| `conflicting packages` | undo (step 8) |

## 4. Swap the camera (power OFF, CSI is not hot-pluggable)
`sudo shutdown now`, wait for the LED, unplug power. Remove the CM3 cable from CAM/DISP 0.
- Narrow 22-pin socket on the AR0234 board: 22-pin to 22-pin cable. Wide 15-pin socket: Pi 5 15-to-22 cable (narrow end at the Pi).
- Pi end: CAM/DISP 0, gold contacts toward the Ethernet port, latch closed. Camera end: contacts toward the camera board.

## 5. config.txt
```bash
sudo nano /boot/firmware/config.txt
```
- change `camera_auto_detect=1` to `camera_auto_detect=0`
- at the very end (under `[all]`) add, **with no leading space**:
  ```
  dtoverlay=arducam-pivariety,cam0
  ```
- save, then `sudo reboot` (the overlay only loads after a reboot)

## 6. Test the camera
```bash
dmesg | grep -i pivariety                  # firmware version / sensor lines, no error
rpicam-hello --list-cameras                # 0 : arducam-pivariety ... modes 1920x1200 / 1920x1080 / 1280x720
rpicam-still -t 3000 -n -o rgb.jpg
rpicam-vid -t 5000 -n --codec mjpeg --width 1280 --height 720 --framerate 15 -o rgb.mjpeg
```
Our result: detected 1920x1200 GRBG, firmware 0x10004, normal colours.

| Problem | Fix |
|---|---|
| `ERROR IPAProxy ... arducam-pivariety.json not found` | harmless (Arducam says ignore) |
| `no cameras available` + `error -5` / `-121` / `failed to read` in dmesg | cable: power off, reseat both ends, check contact direction |
| no pivariety lines in dmesg at all | overlay not loaded: `grep -n -E "camera_auto_detect\|pivariety" /boot/firmware/config.txt` must show `camera_auto_detect=0` and the overlay line without `#` or leading space; reboot |
| still nothing | try `,cam1`, or no port suffix (`dtoverlay=arducam-pivariety`), reboot |
| listed but black image | `rpicam-still -t 3000 -n --shutter 20000 --gain 4 -o rgb.jpg`, more light |
| very green / purple | colour tuning missing; detection still works |
| `Device or resource busy` | `pkill rpicam; pkill -f garuda_mono.py; sudo systemctl stop garuda-mono` |

## 7. Run Garuda with the AR0234
```bash
hailortcli fw-control identify            # HAILO10H, 5.3.0 - unchanged
```
The AR0234 runs as the CSI camera: `GARUDA_MONO_CAM=csi` (already in `booth/garuda-mono.env`; depth off with
`GARUDA_MONO_SKIP_DEPTH=1`). Start it with the booth service (`bash ~/booth/booth_setup_once.sh`, or
`sudo systemctl restart garuda-mono`) and wait for `capture: csi (rpicam-vid)` in `~/mono.log`.
Open `http://<pi>:8098/demo`: boxes on people, click to select.
Measured on our Pi (2026-11-04): about 14 fps on `/demo`, gestures worked, 49 °C.
Distance numbers are not valid with this lens (the depth models were made for the CM3); detection, tracking and the
on-drone yaw command are fine.

## 8. Undo: back to the CM3
```bash
pkill -f garuda_mono.py; pkill rpicam; sudo systemctl stop garuda-mono 2>/dev/null
sudo cp /boot/firmware/config.txt.cm3-working /boot/firmware/config.txt
cd ~/stock_rollback
sudo dpkg -i --force-downgrade libcamera0.7_*.deb libcamera-ipa_*.deb
sudo dpkg -i --force-downgrade librpicam-app1_*.deb rpicam-apps-core_*.deb rpicam-apps-encoder_*.deb rpicam-apps-preview_*.deb rpicam-apps-opencv-postprocess_*.deb rpicam-apps_*.deb
# only if they were installed before (see ~/camera-before.txt):
sudo dpkg -i --force-downgrade libcamera-dev_*.deb python3-libcamera_*.deb libcamera-tools_*.deb gstreamer1.0-libcamera_*.deb
sudo dpkg -r librpicam-app-dev 2>/dev/null
dpkg -l | grep -E "libcamera0.7|rpicam-apps-core"     # OK = ...-1 versions (stock)
sudo shutdown now      # put the CM3 back on CAM/DISP 0, power on
rpicam-hello --list-cameras                            # imx708 = back to normal
```
Dependency errors during undo: compare with `~/camera-before.txt`; with internet:
`sudo apt-get install --reinstall libcamera0.7 libcamera-ipa rpicam-apps`.
