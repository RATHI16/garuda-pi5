#!/usr/bin/env bash
# Move the Pi hotspot (GarudaBench) to 5 GHz channel 36 - smooth /demo page (2.4 GHz ch 6 was too crowded).
# SAFE: if no laptop reconnects within 5 minutes it goes back to 2.4 GHz channel 6 by itself.
#   bash ~/booth/hotspot_5ghz.sh          (asks the sudo password, then your SSH drops - expected)
#   Then reconnect the laptop WiFi to GarudaBench at once. After 6 min: cat ~/hotspot_5ghz.log
#   KEPT 5 GHz = done for good (stays after reboots).  REVERTED = laptop has no 5 GHz or was too slow.
# Country: change COUNTRY below if you are not in India (5 GHz channels depend on it).
set -u
COUNTRY=IN
LOG=/home/garuda/hotspot_5ghz.log

if [ "${1:-}" != "--detached" ]; then
  sudo -v || exit 1
  echo "Switching to 5 GHz in the background. Your SSH will drop: reconnect the laptop to GarudaBench NOW."
  sudo setsid nohup bash "$0" --detached > "$LOG" 2>&1 < /dev/null &
  exit 0
fi

AP=$(nmcli -t -f NAME,TYPE con show | while IFS=: read -r n t; do
       [ "$t" = 802-11-wireless ] && [ "$(nmcli -g 802-11-wireless.mode con show "$n")" = ap ] && echo "$n"; done | head -1)
[ -n "$AP" ] || { echo "FAIL no hotspot profile (run booth_setup_once.sh first)"; exit 1; }
echo "hotspot profile: $AP"
raspi-config nonint do_wifi_country "$COUNTRY"
nmcli con modify "$AP" 802-11-wireless.band a 802-11-wireless.channel 36
nmcli con up "$AP"
sleep 300
if [ "$(iw dev wlan0 station dump | grep -c ^Station)" -eq 0 ]; then
  nmcli con modify "$AP" 802-11-wireless.band bg 802-11-wireless.channel 6
  nmcli con up "$AP"
  echo "REVERTED to 2.4 GHz (no laptop connected on 5 GHz within 5 min)"
else
  echo "KEPT 5 GHz"
fi
nmcli -g 802-11-wireless.band,802-11-wireless.channel con show "$AP"
