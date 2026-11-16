#!/bin/bash
# Reversible host-stereo deployment; no package/driver/boot edits.
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
conf=/etc/systemd/system/garuda-oak-preview.service.d/30-garuda-host-stereo.conf
# --disable now means capture-only, never a fallback to RVC2 depth.
mode=host
case "${1:-}" in
 "") ;;
 --capture-only|--disable) mode=capture ;;
 *) echo 'Usage: enable-host-stereo.sh [--capture-only|--disable]' >&2; exit 2 ;;
esac
capture_args=()
if [ "$mode" = host ]; then capture_args=(--host-stereo); fi
release=/home/garuda/garuda-host-stereo/$(date +%s%N)
install -d "$release"
for f in preview.py pairing.py geometry.py imu_stream.py video_link.py host_capture.py host_stereo.py hailo_host_stereo.py host_flow.py; do install -m 0644 "$root/$f" "$release/$f"; done
PYTHONPATH=/usr/lib/python3/dist-packages /home/garuda/.venvs/oak-bringup-3.12.1/bin/python -c 'import cv2,depthai,numpy'
backup=$(mktemp);had=0
if sudo test -f "$conf"; then sudo cat "$conf" > "$backup";had=1;fi
committed=0
cleanup(){
 status=$?
 trap - EXIT HUP INT TERM
 if [ "$committed" != 1 ]; then
  if [ "$had" = 1 ]; then sudo install -m 0644 "$backup" "$conf";else sudo rm -f "$conf";fi
  sudo systemctl daemon-reload
  sudo systemctl restart garuda-oak-preview garuda-mono
 fi
 rm -f "$backup"
 exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
sudo systemctl stop garuda-mono garuda-oak-preview
sudo mkdir -p "$(dirname "$conf")"
printf '%s\n' '[Service]' 'Environment=PYTHONPATH=/usr/lib/python3/dist-packages' 'RestartPreventExitStatus=78' 'KillSignal=SIGINT' 'ExecStart=' "ExecStart=/home/garuda/.venvs/oak-bringup-3.12.1/bin/python -u $release/preview.py --port 8096 ${capture_args[*]}" | sudo tee "$conf" >/dev/null
sudo systemctl daemon-reload
sudo systemctl start garuda-oak-preview
python3 - "$mode" <<'PY'
import json,time,urllib.request,sys
mode=sys.argv[1]
for _ in range(40):
 try:
  h=json.load(urllib.request.urlopen('http://127.0.0.1:8096/health',timeout=2))
  if mode=='host' and h.get('healthy') and h.get('depth_backend')=='pi-sgbm':break
  if mode=='capture' and h.get('stereo_enabled') is False and h.get('rgb_undistorted') is False and not h.get('error') and h.get('raw_seq',-1)>=0 and h.get('age_s') is not None and 0<=h['age_s']<1:break
 except Exception:pass
 time.sleep(1)
else:raise SystemExit('requested camera mode not healthy')
print(json.dumps(h))
PY
sudo systemctl start garuda-mono
sleep 3
sudo systemctl is-active --quiet garuda-mono
committed=1
printf '%s\n' "$release"
