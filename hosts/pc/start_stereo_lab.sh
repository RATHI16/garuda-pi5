#!/usr/bin/env bash
# Start the isolated PX4 + Gazebo lab with the real bench camera's stereo as the follow source.
# Simulation only. Run from a desktop terminal (Gazebo opens a window). Stop any other lab first:
# this refuses to start while another simulator, controller or dashboard is running.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
export GARUDA_SIM=1 GZ_PARTITION=garuda_isolated
# Reuse the ROS environment that already exists in the px4-gazebo-demo worktree unless told otherwise.
export GARUDA_PIXI_MANIFEST=${GARUDA_PIXI_MANIFEST:-$ROOT/../px4-gazebo-demo/pixi.toml}
# Explicit opt-in: the stereo distance is an unvalidated preview and the camera pose is assumed.
export GARUDA_ACCEPT_PREVIEW_STEREO=1
# Demo choice (owner, 2026-11-14): when the person's track ID changes, a clear clothing-appearance
# match may continue the follow without a new click. Set to 0 for strict reselection.
export GARUDA_STEREO_REACQUIRE=${GARUDA_STEREO_REACQUIRE-1}
# The camera's demo page (served by the Pi) may command this simulator from the browser when its
# 'Link PX4 lab' switch is on. Only this one origin; set to empty to refuse.
export GARUDA_LAB_ALLOW_ORIGIN=${GARUDA_LAB_ALLOW_ORIGIN-http://10.42.0.128:8098}
if pgrep -f "start_sitl.py|sitl_demo.py|bin/px4 " >/dev/null; then
  echo "Another simulator or controller is running. Land, then stop it first:" >&2
  pgrep -af "start_sitl.py|sitl_demo.py|bin/px4 " | cut -c1-150 >&2; exit 1
fi
if curl -s -m 1 -o /dev/null http://127.0.0.1:8099/api/state; then echo "Port 8099 is already serving a dashboard." >&2; exit 1; fi
mkdir -p .runtime/processes
CFG=.runtime/lab-config.json
PX4=$(python3 -c "import json;print(json.load(open('$CFG'))['px4'])"); ACTOR=$(python3 -c "import json;print(json.load(open('$CFG'))['actor'])")
setsid nohup python3 hosts/pc/start_sitl.py --visual --external-person --px4 "$PX4" --actor "$ACTOR" > .runtime/processes/sitl.log 2>&1 &
echo "simulator starting (log .runtime/processes/sitl.log)"
pgrep -f "MicroXRCEAgent udp4 -p 8889" >/dev/null || python3 hosts/pc/lab_processes.py start --only agent
sleep 12
python3 hosts/pc/lab_processes.py start --only dashboard
python3 hosts/pc/lab_processes.py start --only person
# QGroundControl with its own settings folder; the H.265 UDP source is set on first use only.
if ! pgrep -f "[Q]GroundControl" >/dev/null; then
  QGC=$(python3 -c "import json;print(json.load(open('$CFG'))['qgc'])")
  INI=.runtime/qgc-config/QGroundControl/QGroundControl.ini
  mkdir -p "$(dirname "$INI")" .runtime/qgc-data
  grep -q '^\[Video\]' "$INI" 2>/dev/null || printf '\n[Video]\nvideoSource=UDP h.265 Video Stream\nudpUrl=0.0.0.0:5600\n' >> "$INI"
  XDG_CONFIG_HOME=$ROOT/.runtime/qgc-config XDG_DATA_HOME=$ROOT/.runtime/qgc-data setsid nohup "$QGC" $([[ $QGC == *.AppImage ]] && echo --appimage-extract-and-run) > .runtime/processes/qgc.log 2>&1 &
  echo "QGroundControl starting (H.265 video on UDP 5600)"
fi
echo "Dashboard: http://127.0.0.1:8099  ->  Source: 'Live Pi · OAK stereo distance (unvalidated)', Load source, click yourself, Take off."
