#!/usr/bin/env bash
# EVERY TIME on the Pi:
#   bash ~/booth/booth_fly.sh dry    -> checks + bridge DRY RUN (sends nothing)
#   bash ~/booth/booth_fly.sh        -> checks + bridge LIVE (props-off bench, then flight)
#   bash ~/booth/booth_fly.sh stop   -> stop the bridge
# The bridge never arms or changes mode: the pilot switches OFFBOARD on the RC.
set -u
MODE=${1:-live}
F=/home/garuda/garuda-sdk-deploy/services/garuda-follow
FC=192.168.100.3
LIMITS="--no-yaw-limit --max-yaw-rate 0.25"
ok()   { echo "  OK   $*"; }
fail() { echo "  FAIL $*"; exit 1; }

tmux kill-session -t bridge 2>/dev/null
pkill -f booth_yaw_bridge.py; pkill -f fc_bridge.py; pkill -f mav_eth_test.py; pkill -f offboard_
sleep 1
[ "$MODE" = stop ] && { echo "bridge stopped"; exit 0; }

echo "== 1. Pi health"
t=$(vcgencmd get_throttled | cut -d= -f2)
[ "$t" = 0x0 ] && ok "power/heat OK (throttled=0x0), $(vcgencmd measure_temp)" || echo "  WARN throttled=$t (power supply too weak or too hot) $(vcgencmd measure_temp)"

echo "== 2. Camera service"
systemctl is-active --quiet garuda-mono || { sudo systemctl restart garuda-mono; sleep 15; }
c=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8098/api/state)
[ "$c" = 200 ] && ok "camera answering" || { echo "  FAIL camera not answering:"; tail -20 /home/garuda/mono.log; exit 1; }

echo "== 3. FC link (Ethernet MAVLink, 5 s)"
ping -c 2 -W 1 $FC >/dev/null && ok "FC $FC replies to ping" || fail "no ping to $FC: Ethernet cable / FC power"
SYSID=$(python3 - <<'EOF'
from pymavlink import mavutil; import time
m = mavutil.mavlink_connection('udpin:0.0.0.0:14540'); t = time.time(); got = set(); sysid = None
while time.time() - t < 5:
    x = m.recv_match(blocking=True, timeout=1)
    if x is None: continue
    got.add(x.get_type())
    if x.get_type() == 'HEARTBEAT' and x.autopilot == mavutil.mavlink.MAV_AUTOPILOT_PX4 and x.get_srcComponent() == 1:
        sysid = x.get_srcSystem()
need = {'HEARTBEAT', 'ATTITUDE', 'LOCAL_POSITION_NED'}
print(sysid if sysid is not None and need <= got else "NONE:" + ",".join(sorted(need - got)))
EOF
)
case "$SYSID" in
  NONE:*) echo "  FAIL FC data missing: ${SYSID#NONE:}"
          echo "       nothing at all -> QGC MAVLink console:  mavlink stop -u 14550"
          echo "                                                mavlink start -u 14540 -o 14540 -t 192.168.100.12 -m onboard -r 20000"
          echo "       only LOCAL_POSITION_NED missing -> flow sensor / position not valid (uavcan status, node 125)"
          exit 1 ;;
esac
ok "FC sends heartbeat + attitude + position, system ID $SYSID"

echo "== 4. Bridge"
DRY=""; [ "$MODE" = dry ] && DRY="--dry-run"
CMD="cd $F && GARUDA_FC_BRIDGE=1 python3 booth_yaw_bridge.py --real-flight-controller $DRY --listen 0.0.0.0:14540 --fc-host $FC --fc-sysid $SYSID $LIMITS; echo; echo BRIDGE STOPPED; sleep 100000"
tmux new -d -s bridge "$CMD"
sleep 3
tmux has-session -t bridge 2>/dev/null && ok "bridge running ($([ -n "$DRY" ] && echo "DRY RUN, sends nothing" || echo LIVE)), no yaw limit"
echo
echo "Now opening the bridge window.  Leave it: Ctrl+B then D.   Come back: tmux attach -t bridge"
sleep 2
exec tmux attach -t bridge
