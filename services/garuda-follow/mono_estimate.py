"""Simulation-only fixed bench camera mapping. No metric depth claim.

Box height/centre drive an illustrative virtual position. The displayed mono model
estimate is independent and is deliberately not treated as calibrated localization.
"""
import math


def target(snapshot, track, home, accepted):
    if snapshot.get('stats', {}).get('capabilities', {}).get('mount') == 'vehicle':
        return None, 'Camera is on the vehicle • fixed-camera mapping refused'
    if not accepted:
        return None, 'Mono estimate not accepted • use --accept-mono-estimate-in-simulation'
    caps = snapshot.get('stats', {}).get('capabilities', {})
    if caps.get('camera') not in ('cm3', 'usb') or caps.get('distance_source') != 'mono_estimate':
        return None, 'Mono camera capability missing • hold (no stereo fallback)'
    if snapshot.get('stereo') is not None:
        return None, 'Stereo payload in mono source • choose the explicit stereo source'
    frame = snapshot.get('frame', {})
    if not snapshot.get('stats', {}).get('instance') or not frame.get('epoch') or type(frame.get('seq')) is not int:
        return None, 'Mono observation identity missing • hold'
    vals = [frame.get('width'), frame.get('height'), track.get('x'), track.get('y'), track.get('w'), track.get('h')]
    if not all(type(v) in (int, float) and math.isfinite(v) for v in vals):
        return None, 'Invalid mono box geometry • hold'
    width, height, x, y, bw, bh = vals
    if width <= 0 or height <= 0 or bw <= 0 or bh <= 0 or x < 0 or y < 0 or x+bw > width or y+bh > height:
        return None, 'Incomplete mono person box • hold'
    depth = max(1.8, min(7., 5.5 - bh/height*4))
    return (home[0]+depth-.8, home[1]+((x+bw/2)/width-.5)*7), 'Mono estimate • schematic box mapping, not measured distance'


def provenance(accepted):
    return dict(source='mono_estimate', method='box-size-schematic-v1',
                measurement_qualified=False, simulation_only=True,
                accepted=bool(accepted), camera_pose='assumed-fixed-bench-camera')
