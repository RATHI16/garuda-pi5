#!/bin/bash
# Explicit depth preview only. Caller must hold the shared Pi coordination lock.
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
unit=garuda-depth-session.service
if [ "${1:-}" = --stop ]; then sudo systemctl stop "$unit"; exit; fi
seconds=${1:-60}
[[ "$seconds" =~ ^[0-9]+$ ]] && ((seconds>=1 && seconds<=600))
# Optional: least milliseconds between full depth maps (0 = every pair). Under 1000 so the map never reads as stale.
map_interval=${2:-0}
[[ "$map_interval" =~ ^[0-9]+$ ]] && ((map_interval<=900))
# Optional third argument 'demand': full maps only while something reads them.
demand=()
if [ "${3:-}" = demand ]; then demand=(--map-on-demand); elif [ -n "${3:-}" ]; then echo 'third argument must be: demand' >&2; exit 1; fi
for active in "$unit" garuda-stereo-trial.service; do
 if systemctl is-active --quiet "$active"; then echo 'A depth trial is already active' >&2;exit 1;fi
done
# Restoration must return to capture-only, never an older stereo configuration.
command=$(systemctl show garuda-oak-preview -p ExecStart --value)
[[ "$command" == *preview.py* && "$command" != *--host-stereo* && "$command" != *--stereo* ]]
systemctl is-active --quiet garuda-oak-preview
python3 "$root/depth_session.py" --preflight
release=/home/garuda/garuda-depth-sessions/$(date +%s%N)
install -d "$release"
for f in preview.py pairing.py geometry.py imu_stream.py video_link.py host_capture.py host_stereo.py host_flow.py hailo_host_stereo.py depth_session.py;do install -m0644 "$root/$f" "$release/$f";done
# No persistent override. ExecStopPost also restores capture after a crash or kill;
# --no-block avoids an ordering deadlock with this conflicting unit stopping.
sudo systemd-run --unit=garuda-depth-session --collect \
 --property=User=garuda --property=KillSignal=SIGINT --property=TimeoutStopSec=15 \
 --property="RuntimeMaxSec=$((seconds+30))" \
 --property='Conflicts=garuda-oak-preview.service' --property='After=garuda-oak-preview.service' \
 --property='ExecStopPost=+/usr/bin/systemctl --no-block start garuda-oak-preview.service' \
 --setenv=PYTHONPATH=/usr/lib/python3/dist-packages \
 /home/garuda/.venvs/oak-bringup-3.12.1/bin/python -u "$release/depth_session.py" --seconds "$seconds" --map-interval-ms "$map_interval" "${demand[@]}"
printf 'Depth preview session requested for at most %s seconds. Stop: %s --stop\n' "$seconds" "$0"
