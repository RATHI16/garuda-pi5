#!/usr/bin/env python3
"""Optional low-bandwidth camera stream for the laptop page (local tweak to Bhanu's garuda_mono.py).

Adds three settings; when they are NOT set, the program behaves exactly like Bhanu's original:
  GARUDA_MONO_STREAM_W=640       width of the picture sent to the browser (0 = full size, original)
  GARUDA_MONO_STREAM_Q=60        JPEG quality of that picture (original 75)
  GARUDA_MONO_STREAM_NEW_ONLY=1  send a picture only when it is new (original re-sends every 40 ms)
Only the picture sent to the browser changes. Detection, tracking, re-ID and the drone command
still use the full camera frame.

Run on the Pi:  python3 ~/booth/patch_stream_lowbw.py
Undo:           cp ~/garuda-sdk-deploy/hosts/pi5/garuda_mono.py.bhanu ~/garuda-sdk-deploy/hosts/pi5/garuda_mono.py
"""
import os
import shutil
import sys

path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/garuda-sdk-deploy/hosts/pi5/garuda_mono.py")
src = open(path, encoding="utf-8").read()
if "GARUDA_MONO_STREAM_W" in src:
    print("already patched:", path)
    sys.exit(0)

A_OLD = '''            if _streams["clean"]:
                ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])'''
A_NEW = '''            if _streams["clean"]:
                view = img
                if 0 < STREAM_W < img.shape[1]:          # local tweak: smaller picture for the browser only
                    view = cv2.resize(img, (STREAM_W, int(img.shape[0] * STREAM_W / img.shape[1])), interpolation=cv2.INTER_AREA)
                ok, jpg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, STREAM_Q])'''

B_OLD = '''                try:
                    while True:
                        b = _jpg.get("clean" if kind == "clean" else "buf")
                        if b:
                            self.wfile.write(b"--frame\\r\\nContent-Type: image/jpeg\\r\\nContent-Length: " +
                                             str(len(b)).encode() + b"\\r\\n\\r\\n" + b + b"\\r\\n")
                        time.sleep(0.04)'''
B_NEW = '''                try:
                    last_b = None
                    while True:
                        b = _jpg.get("clean" if kind == "clean" else "buf")
                        if b and not (STREAM_NEW_ONLY and b is last_b):   # local tweak: skip repeats
                            self.wfile.write(b"--frame\\r\\nContent-Type: image/jpeg\\r\\nContent-Length: " +
                                             str(len(b)).encode() + b"\\r\\n\\r\\n" + b + b"\\r\\n")
                            last_b = b
                        time.sleep(0.04)'''

C_OLD = '''CAM_W, CAM_H, CAM_FPS = 1280, 720, 15
'''
C_NEW = '''CAM_W, CAM_H, CAM_FPS = 1280, 720, 15
# local tweak (booth): browser picture size/quality; unset = Bhanu's original behaviour
STREAM_W = int(os.environ.get("GARUDA_MONO_STREAM_W", "0"))
STREAM_Q = int(os.environ.get("GARUDA_MONO_STREAM_Q", "75"))
STREAM_NEW_ONLY = os.environ.get("GARUDA_MONO_STREAM_NEW_ONLY") == "1"
'''

for old, name in ((A_OLD, "clean JPEG encode"), (B_OLD, "stream loop"), (C_OLD, "camera size line")):
    if src.count(old) != 1:
        print("FAIL: could not find the", name, "in", path, "- this is not the expected Bhanu version; nothing changed")
        sys.exit(1)
backup = path + ".bhanu"
if not os.path.exists(backup):
    shutil.copy2(path, backup)
src = src.replace(A_OLD, A_NEW).replace(B_OLD, B_NEW).replace(C_OLD, C_NEW)
open(path, "w", encoding="utf-8", newline="\n").write(src)
print("patched:", path, "  (original saved as", backup + ")")
