#!/bin/bash
# Explicit bench operator action. Keeps the original owner file as rollback.
set -euo pipefail
source_root=$(cd "$(dirname "$0")" && pwd)
target_root=/home/garuda/garuda-oak-owner
conf=/etc/systemd/system/garuda-oak-preview.service.d/20-garuda-stereo.conf
if [ "${1:-}" = --disable ]; then
 sudo rm -f "$conf"
 sudo systemctl daemon-reload
 sudo systemctl restart garuda-oak-preview garuda-mono
 exit
fi
install -d "$target_root"
for file in preview.py pairing.py geometry.py imu_stream.py video_link.py; do install -m 0644 "$source_root/$file" "$target_root/$file"; done
backup=$(mktemp)
had_conf=0
if sudo test -f "$conf"; then sudo cat "$conf" > "$backup"; had_conf=1; fi
rollback(){
 if [ "$had_conf" = 1 ]; then sudo install -m 0644 "$backup" "$conf"; else sudo rm -f "$conf"; fi
 sudo systemctl daemon-reload
 sudo systemctl restart garuda-oak-preview garuda-mono
 rm -f "$backup"
}
trap rollback ERR
sudo systemctl stop garuda-mono garuda-oak-preview
sudo mkdir -p "$(dirname "$conf")"
printf '%s\n' '[Service]' 'ExecStart=' "ExecStart=/home/garuda/.venvs/oak-bringup-3.12.1/bin/python -u $target_root/preview.py --port 8096 --stereo" | sudo tee "$conf" >/dev/null
sudo systemctl daemon-reload
sudo systemctl start garuda-oak-preview
python3 - <<'READY'
import json,time,urllib.request
for _ in range(30):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8096/health',timeout=2) as r:h=json.load(r)
  if h.get('stereo_enabled') and h.get('healthy'):break
 except Exception:pass
 time.sleep(1)
else:raise SystemExit('stereo startup failed')
print('Stereo ready; USB:',h.get('usb'))
READY
sudo systemctl start garuda-mono
trap - ERR
rm -f "$backup"
