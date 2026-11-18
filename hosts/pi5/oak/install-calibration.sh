#!/bin/bash
# Install separate read-only-to-camera calibration workspace. Does not alter OAK/mono services.
set -euo pipefail
source_root=$(cd "$(dirname "$0")/.." && pwd)
target_root=/home/garuda/garuda-calibration
handouts=(demo/chennai/OPERATOR.md demo/chennai/CLAUDE_HANDOFF.md demo/chennai/oak-targets-a4.pdf)
for file in "${handouts[@]}" oak_calibration.py oak_depth_view.py oak_pair.py oak/geometry.py demo/calibration-centre.html demo/centre.js demo/calibration.html demo/calibration.js demo/calibration.css demo/calibration-guide.js oak/garuda-calibration.service; do test -f "$source_root/$file"; done
if systemctl cat garuda-calibration.service >/dev/null 2>&1; then sudo systemctl stop garuda-calibration.service; fi
install -d "$target_root/demo/chennai" "$target_root/oak"
install -m 0644 "$source_root/oak_calibration.py" "$source_root/oak_pair.py" "$source_root/oak_depth_view.py" "$target_root/"
install -m 0644 "$source_root/oak/geometry.py" "$target_root/oak/"
for file in calibration-centre.html centre.js calibration.html calibration.js calibration.css calibration-guide.js; do install -m 0644 "$source_root/demo/$file" "$target_root/demo/$file"; done
for file in "${handouts[@]}"; do install -m 0644 "$source_root/$file" "$target_root/$file"; done
sudo install -m 0644 "$source_root/oak/garuda-calibration.service" /etc/systemd/system/garuda-calibration.service
sudo systemctl daemon-reload
sudo systemctl enable garuda-calibration.service
sudo systemctl restart garuda-calibration.service

# Patch only the navigation link into the currently deployed demo; preserve all its behavior.
python3 - <<'LINK'
from pathlib import Path
import shutil,time
root=Path('/home/garuda/garuda-sdk-deploy/hosts/pi5/demo')
index=root/'index.html';app=root/'app.js'
if index.exists() and app.exists():
 html=index.read_text();js=app.read_text()
 if 'id="calibrationLink"' not in html:
  if '<button id="fullscreen"' not in html:raise SystemExit('Demo header differs; service installed, navigation patch not applied')
  backup=Path.home()/'.local/share/garuda/calibration-install-backup'/str(time.time_ns())
  backup.mkdir(parents=True,mode=0o700)
  shutil.copy2(index,backup/'index.html');shutil.copy2(app,backup/'app.js')
  prefix="{const u=new URL(location.href);u.port='8099';u.pathname='/';u.search='';u.hash='';const link=document.getElementById('calibrationLink');if(link)link.href=u.href;}\n"
  app.write_text(js.replace("'use strict';", "'use strict';\n"+prefix, 1) if js.startswith("'use strict';") else prefix+js)
  html=html.replace('<button id="fullscreen"','<a id="calibrationLink" class="headerbutton" target="_blank" rel="noopener" href="http://localhost:8099/">Calibration ↗</a><button id="fullscreen"',1)
  index.write_text(html)
  print('Navigation backups:',backup)
LINK
