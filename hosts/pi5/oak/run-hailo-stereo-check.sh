#!/bin/bash
# Explicit exclusive live bench run. Requires the coordination lock and a cool Pi.
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
hef=${1:?Usage: run-hailo-stereo-check.sh HEF [seconds]}
seconds=${2:-60}
[[ "$seconds" =~ ^[0-9]+$ ]] && ((seconds>=1 && seconds<=600))
test -f "$hef"
if systemctl is-active --quiet garuda-stereo-trial.service; then echo 'A trial already owns the camera' >&2;exit 1;fi
python3 - <<'PY'
import subprocess
flags=int(subprocess.check_output(['vcgencmd','get_throttled'],text=True).strip().split('=')[1],16)
if flags&15:raise SystemExit('Active limiting flags: cool the Pi before a trial')
PY
release=/home/garuda/garuda-hailo-trials/$(date +%s%N)
install -d "$release"
for f in preview.py pairing.py geometry.py imu_stream.py video_link.py host_capture.py host_stereo.py host_flow.py hailo_host_stereo.py;do install -m0644 "$root/$f" "$release/$f";done
restore(){
 trap - EXIT HUP INT TERM
 sudo systemctl stop garuda-stereo-trial.service || true
 sudo systemctl start garuda-oak-preview garuda-mono
}
trap restore EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
sudo systemctl stop garuda-mono garuda-oak-preview
sudo systemd-run --unit=garuda-stereo-trial --collect --property=User=garuda --property=KillSignal=SIGINT --property=RestartPreventExitStatus=78 --property='Conflicts=garuda-mono.service garuda-mono-trial.service garuda-oak-preview.service' --property='After=garuda-mono.service garuda-mono-trial.service garuda-oak-preview.service' --working-directory="$release" --setenv=GARUDA_EXCLUSIVE_STEREO=1 --setenv=PYTHONPATH=/usr/lib/python3/dist-packages:/home/garuda/.local/lib/python3.13/site-packages /home/garuda/.venvs/oak-bringup-3.12.1/bin/python -u "$release/preview.py" --host-stereo --stereo-hef "$hef"
python3 - "$seconds" "$release" <<'PY'
import json,sys,time,subprocess,urllib.request
from pathlib import Path
rows=[];cursor=-1;events=[]
for _ in range(int(sys.argv[1])):
 start=time.monotonic();row={'host_ns':time.monotonic_ns()}
 try:
  with urllib.request.urlopen('http://localhost:8096/health',timeout=2) as r:row['camera']=json.load(r)
  with urllib.request.urlopen('http://localhost:8096/timing?after='+str(cursor),timeout=2) as r:trace=json.load(r)
  row['trace_overrun']=trace['overrun'];events+=trace['records'];cursor=trace['next_id']-1
 except Exception as e:row['error']=str(e)
 row['throttle']=subprocess.check_output(['vcgencmd','get_throttled'],text=True).strip()
 row['thermal']=subprocess.check_output(['vcgencmd','measure_temp'],text=True).strip();rows.append(row)
 if int(row['throttle'].split('=')[1],16)&15:break
 time.sleep(max(0,1-(time.monotonic()-start)))
Path(sys.argv[2],'trial.json').write_text(json.dumps(dict(rows=rows,events=events),indent=2))
print(sys.argv[2],flush=True)
PY
