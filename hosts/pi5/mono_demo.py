"""Local-only demo assets; fixed routes, no user-controlled filesystem paths."""
from pathlib import Path

ROOT = Path(__file__).with_name("demo")
ASSETS = {
    "/demo": ("index.html", "text/html; charset=utf-8"),
    "/demo/": ("index.html", "text/html; charset=utf-8"),
    "/demo/style.css": ("style.css", "text/css"),
    "/demo/controller.js": ("controller.js", "text/javascript"),
    "/demo/depth-inspector.js": ("depth-inspector.js", "text/javascript"),
    "/demo/app.js": ("app.js", "text/javascript"),
    "/demo/controls.js": ("controls.js", "text/javascript"),
    "/demo/flight-setup.js": ("flight-setup.js", "text/javascript"),
    "/demo/lab.js": ("lab.js", "text/javascript"),
    "/demo/controls.css": ("controls.css", "text/css"),
    "/demo/microchip.png": ("microchip.png", "image/png"),
    "/demo/cloud": ("cloud.html", "text/html; charset=utf-8"),
    "/demo/depth-view.js": ("depth-view.js", "text/javascript"),
    "/demo/cloud.js": ("cloud.js", "text/javascript"),
}

def asset(path):
    entry = ASSETS.get(path)
    if entry is None:
        return None
    name, mime = entry
    return mime, (ROOT / name).read_bytes()
