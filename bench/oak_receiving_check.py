#!/usr/bin/env python3
"""Read the OAK shipping profile and compare the Pi handouts to this checkout."""
import argparse
import hashlib
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

HANDOUTS = Path(__file__).resolve().parents[1] / 'hosts/pi5/demo/chennai'


def get(url):
    with urlopen(url, timeout=4) as response:
        return response.read()


def inspect(host, read=get, wait=time.sleep):
    report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'host': host,
              'errors': [], 'handout_sha256': {}}

    def fetch(port, path, as_json=False):
        try:
            body = read(f'http://{host}:{port}{path}')
            if not as_json:
                return body
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError('expected a JSON object')
            return value
        except (OSError, ValueError) as exc:
            report['errors'].append(f'{port}{path}: {exc}')
            return None

    before = fetch(8096, '/health', True) or {}
    demo = fetch(8098, '/api/state', True) or {}
    wait(1)
    owner = fetch(8096, '/health', True) or {}
    report['owner'] = {k: owner.get(k) for k in (
        'usb', 'camera_compute', 'depth_backend', 'stereo_enabled', 'map_on_demand',
        'map_interval_ms', 'imu_enabled', 'measurement_qualified', 'calibration_id',
        'runtime_versions', 'source_hashes', 'error', 'depth_error')}
    first, last = before.get('raw_seq'), owner.get('raw_seq')
    advancing = (bool(before.get('epoch')) and before['epoch'] == owner.get('epoch')
                 and type(first) is int and type(last) is int and last > first)
    stats = demo.get('stats') or {}
    report['demo'] = {'source': (demo.get('frame') or {}).get('source'),
                      'inferences_at_snapshot': stats.get('inferences'), 'temperature_c': stats.get('temp_c')}
    checks = {'same_epoch_rgb_advanced': advancing,
              'oak_capture_encoding_only': owner.get('camera_compute') == 'capture-and-encoding-only',
              'pi_sgbm_stereo': owner.get('depth_backend') == 'pi-sgbm' and owner.get('stereo_enabled') is True,
              'owner_has_no_reported_error': bool(owner) and not (owner.get('error') or owner.get('depth_error')),
              'demo_reports_oak_rgb': report['demo']['source'] == 'oak-rgb'}
    for name in ('oak-targets-a4.pdf', 'OPERATOR.md', 'CLAUDE_HANDOFF.md'):
        expected = hashlib.sha256((HANDOUTS/name).read_bytes()).hexdigest()
        body = fetch(8099, '/chennai/'+name)
        actual = hashlib.sha256(body).hexdigest() if body is not None else None
        report['handout_sha256'][name] = {'checkout': expected, 'served': actual}
        checks[name] = actual == expected
    report['checks'] = checks
    report['shipping_profile_observed'] = not report['errors'] and all(checks.values())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='Pi hostname or IPv4 address')
    parser.add_argument('--out', required=True, type=Path, help='new JSON evidence file')
    args = parser.parse_args()
    try:
        socket.getaddrinfo(args.host, None, socket.AF_INET)
    except OSError as exc:
        parser.error(str(exc))
    with args.out.open('x') as dest:
        result = inspect(args.host)
        json.dump(result, dest, indent=2)
        dest.write('\n')
    for name, ok in result['checks'].items():
        print(f"{'OK' if ok else 'CHECK'}  {name}")
    return 0 if result['shipping_profile_observed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
