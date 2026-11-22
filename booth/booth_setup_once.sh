#!/usr/bin/env bash
# ONE-TIME setup on the Pi:  bash ~/booth/booth_setup_once.sh
# After this, every power-on brings up by itself:
#   GarudaBench WiFi hotspot (10.43.0.1) + Ethernet to the FC (192.168.100.12) + the camera service.
# Run it again any time; it is safe to repeat. Undo: see the end of FLY_STEPS.txt.
set -u
B=/home/garuda/booth
D=/home/garuda/garuda-sdk-deploy
ok()   { echo "  OK   $*"; }
fail() { echo "  FAIL $*"; exit 1; }

echo "== 1. Files"
[ -f "$D/hosts/pi5/garuda_mono.py" ] || fail "$D/hosts/pi5/garuda_mono.py missing"
grep -q "GARUDA_MONO_MOUNT" "$D/hosts/pi5/garuda_mono.py" || fail "Pi has the OLD camera version; install garuda_new_pi.tar.gz first"
for f in garuda-mono.env garuda-mono.service booth_yaw_bridge.py booth_fly.sh patch_stream_lowbw.py; do
  [ -f "$B/$f" ] || fail "$B/$f missing (copy the booth folder again)"
done
sed -i 's/\r$//' "$B"/*.sh "$B"/*.env "$B"/*.service "$B"/*.py      # remove Windows line endings
cp "$B/booth_yaw_bridge.py" "$D/services/garuda-follow/" && ok "bridge copied to $D/services/garuda-follow/"
grep -q REID_HEF "$B/garuda-mono.env" && echo "  INFO re-ID ON (smoother without it: delete the REID line)" || ok "re-ID off (normal tracking)"
chmod +x "$B"/*.sh
python3 "$B/patch_stream_lowbw.py" || fail "stream patch (laptop video size) did not apply"

echo "== 2. Stop the manual camera (tmux) so the service can own the camera"
tmux kill-session -t mono 2>/dev/null; pkill -f garuda_mono.py; pkill rpicam-vid; sleep 2; ok "stopped"

echo "== 3. Camera service (starts at every boot, restarts itself if it crashes)"
mkdir -p "$B/backup"
[ -f /etc/systemd/system/garuda-mono.service ] && sudo cp /etc/systemd/system/garuda-mono.service "$B/backup/garuda-mono.service.before" && ok "old service saved in $B/backup"
if [ -d /etc/systemd/system/garuda-mono.service.d ]; then
  sudo cp -r /etc/systemd/system/garuda-mono.service.d "$B/backup/" && sudo rm -rf /etc/systemd/system/garuda-mono.service.d && ok "old drop-ins saved in $B/backup and removed"
fi
sudo install -m644 "$B/garuda-mono.service" /etc/systemd/system/garuda-mono.service
sudo systemctl daemon-reload
sudo systemctl enable garuda-mono >/dev/null 2>&1 && ok "enabled at boot"
sudo systemctl restart garuda-mono
for i in $(seq 1 45); do
  c=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8098/api/state)
  [ "$c" = 200 ] && break; sleep 2
done
[ "$c" = 200 ] && ok "camera service answering (200)" || { echo "  FAIL camera service not answering. Last log lines:"; tail -25 /home/garuda/mono.log; exit 1; }

echo "== 4. WiFi hotspot GarudaBench at every boot"
AP=""
while IFS=: read -r name type; do
  [ "$type" = "802-11-wireless" ] || continue
  [ "$(nmcli -g 802-11-wireless.mode con show "$name" 2>/dev/null)" = "ap" ] && AP="$name"
done < <(nmcli -t -f NAME,TYPE con show)
if [ -z "$AP" ]; then
  echo "  no hotspot profile found -> creating it with Bhanu's script (2.4 GHz)"
  [ -n "${GARUDA_AP_PSK:-}" ] || fail "no hotspot yet: run again as  GARUDA_AP_PSK=<your-password> bash ~/booth/booth_setup_once.sh"
  bash "$D/hosts/pi5/setup_wifi_ap.sh" --psk "$GARUDA_AP_PSK" --ssid GarudaBench --country IN --band bg || fail "hotspot setup"
  AP=garuda-ap
fi
sudo nmcli con modify "$AP" connection.autoconnect yes connection.autoconnect-priority 100 && ok "hotspot '$AP' starts at boot (priority 100)"

echo "== 5. Ethernet to the FC at every boot"
if ! nmcli -t -f NAME con show | grep -qx "eth-link"; then
  # static Pi address on the FC Ethernet link; no gateway/DNS so it never takes over internet routing
  sudo nmcli con add type ethernet ifname eth0 con-name eth-link ipv4.method manual        ipv4.addresses 192.168.100.12/24 ipv4.never-default yes ipv6.method link-local        && ok "created eth-link (Pi 192.168.100.12/24 for the FC)" || fail "could not create eth-link"
fi
sudo nmcli con modify eth-link connection.autoconnect yes connection.autoconnect-priority 50 && ok "eth-link (192.168.100.12) starts at boot"
sudo nmcli con up eth-link >/dev/null 2>&1 || echo "  WARN eth-link not up now (FC cable in? FC powered?)"
ip -4 addr show eth0 | grep -q "192.168.100.12" && ok "eth0 has 192.168.100.12 now" || echo "  WARN eth0 has no 192.168.100.12 right now"

echo "== 6. Tools"
python3 -c "import pymavlink" 2>/dev/null && ok "pymavlink installed" || echo "  WARN pymavlink missing: pip install --break-system-packages pymavlink (or the offline wheel)"
command -v tmux >/dev/null && ok "tmux installed" || echo "  WARN tmux missing: sudo apt install tmux"
nmcli -g 802-11-wireless.band con show "$AP" 2>/dev/null | grep -qx a && ok "hotspot on 5 GHz"   || echo "  INFO hotspot on 2.4 GHz. For a smooth /demo page run once: bash ~/booth/hotspot_5ghz.sh"

echo
echo "RESULT: SETUP DONE. From now on: power on -> wait 2 min -> laptop on GarudaBench -> bash ~/booth/booth_fly.sh"
