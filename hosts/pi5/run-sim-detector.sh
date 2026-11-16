#!/bin/bash
# Pi only, explicit evaluation session. Caller holds the shared Pi lock.
set -euo pipefail
[[ ${GARUDA_SIM:-} == 1 ]] || { echo 'GARUDA_SIM=1 required' >&2;exit 1; }
root=$(cd "$(dirname "$0")" && pwd)
for unit in garuda-sim-detector garuda-stereo-trial garuda-mono-trial; do
 if systemctl is-active --quiet "$unit";then echo 'Conflicting NPU trial' >&2;exit 1;fi
done
python3 "$root/oak/depth_session.py" --preflight
evidence=/home/garuda/sim-rpc-evidence/$(date +%s%N)
install -d "$evidence"
prior=$(python3 "$root/sim_restoration.py" prior --out "$evidence/prior.json")
restore=()
if [ "$prior" = active ]; then
 restore=(--property='ExecStopPost=+/usr/bin/systemctl --no-block start garuda-mono.service')
fi
printf 'Restoration evidence: %s (prior state: %s)\n' "$evidence" "$prior"
# Only restart mono if it was active before the trial. A stopped unit stays stopped.
sudo systemd-run --unit=garuda-sim-detector --collect --property=User=garuda \
 --property=RuntimeMaxSec=600 --property=TimeoutStopSec=10 \
 --property='Conflicts=garuda-mono.service garuda-stereo-trial.service garuda-mono-trial.service' \
 --property='After=garuda-mono.service garuda-stereo-trial.service garuda-mono-trial.service' \
 "${restore[@]}" \
 --setenv=GARUDA_SIM=1 /usr/bin/python3 -u "$root/sim_detector_server.py"

printf 'After the trial stops: python3 %q verify --prior %q --out %q\n' "$root/sim_restoration.py" "$evidence/prior.json" "$evidence/restoration.json"
