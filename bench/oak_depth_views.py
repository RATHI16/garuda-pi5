"""Compare RGB, inset, expanded and Fine/Full 3D with one disposable browser on the PC."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import oak_soak

MODES = ('rgb', 'inset', 'expanded', 'fine', 'full')
# Run the same logger revision on the Pi, without installing it on the host.
REMOTE = """
import sys, tempfile, json
from pathlib import Path
scope = {'__name__':'oak_soak'}
exec(compile(sys.stdin.read(), 'oak_soak.py', 'exec'), scope)
with tempfile.TemporaryDirectory() as td:
    scope['run']('http://127.0.0.1:8098', float(sys.argv[1])/60, 1,
                 Path(td)/'run', False, 'http://127.0.0.1:8096',
                 lambda row: print(json.dumps(row), flush=True))
"""


def telemetry(pi, seconds):
    command = shlex.join(['python3', '-c', REMOTE, str(seconds)])
    with Path(oak_soak.__file__).open() as source, subprocess.Popen(
        ['ssh', '-oBatchMode=yes', '-oConnectTimeout=5', '-oServerAliveInterval=5',
         '-oServerAliveCountMax=2', '--', pi, command],
        stdin=source, stdout=subprocess.PIPE, text=True
    ) as child:
        try:
            for line in child.stdout:
                yield json.loads(line)
            if child.wait():
                raise RuntimeError('Pi sampler failed')
        finally:
            if child.poll() is None:
                child.terminate()


def require_cooling(row, limit):
    flags = oak_soak.parse_throttled(row.get('throttled'))
    temp = row.get('vcgencmd_temp_c')
    if flags is None or flags['now'] or temp is None or temp >= limit:
        raise RuntimeError(f"Cooling check stopped the comparison at {temp} °C, flags {row.get('throttled')}")


def open_view(context, base, mode):
    page = context.new_page()
    page.goto(base+'/demo', wait_until='domcontentloaded')
    page.locator('#depthButton').wait_for(state='visible', timeout=15000)
    if mode == 'rgb':
        return page, None
    if mode in ('fine', 'full'):
        # Keep the original RGB viewer open, as when the operator clicks 3D view.
        with page.expect_popup() as popup:
            page.locator('#cloudButton').click()
        page = popup.value
        page.locator('#detail').select_option('4' if mode == 'fine' else '2')
        return page, '#status'
    page.locator('#depthButton').click()
    if mode == 'expanded':
        page.locator('#inspectDepth').click()
        return page, '#inspectFrame'
    return page, '#depthRate'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi', required=True, help='SSH user@host for the Pi')
    parser.add_argument('--base', required=True, help='demo base URL as reached from the PC')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--seconds', type=int, default=30)
    args = parser.parse_args()
    if args.seconds < 5:
        parser.error('use at least five seconds per view for the four-second rate window')
    args.out.mkdir(parents=True, exist_ok=False)
    # Sample before even launching Chrome. Preserve this evidence when an attempt stops.
    preflight, = list(telemetry(args.pi, 1))
    (args.out/'preflight.json').write_text(json.dumps(preflight, indent=2))
    require_cooling(preflight, 75)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        chrome = playwright.chromium.launch(executable_path=shutil.which('google-chrome'))
        for index, mode in enumerate((*MODES, *reversed(MODES)), 1):
            context = chrome.new_context(viewport={'width':1360, 'height':900})
            phase = args.out/f'{index:02}-{mode}'
            phase.mkdir()
            try:
                time.sleep(5)  # previous view demand expires after three seconds
                preflight, = list(telemetry(args.pi, 1))
                (phase/'preflight.json').write_text(json.dumps(preflight, indent=2))
                require_cooling(preflight, 75)
                page, status = open_view(context, args.base.rstrip('/'), mode)
                rows = []
                with (phase/'samples.jsonl').open('x') as out:
                    for row in telemetry(args.pi, args.seconds):
                        row['view'] = mode
                        row['browser_status'] = page.locator(status).text_content(timeout=2000) if status else None
                        out.write(json.dumps(row)+'\n')
                        out.flush()
                        rows.append(row)
                        require_cooling(row, 78)
                result = oak_soak.summarise(rows)
                result['browser_display_observed'] = any('Pair ' in (r['browser_status'] or '') for r in rows) if status else None
                (phase/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
            finally:
                context.close()
        chrome.close()


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
