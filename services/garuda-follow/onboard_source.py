"""Independent consumer of Pi shadow commands, for isolated SITL only.

This source rehearses the mono box-height law with Gazebo pixels. It does not
claim stereo, re-ID validation, calibrated bearing or a real flight interface.
"""
import math

MAX_AGE_MS = 400.
MAX_FORWARD = .8
MAX_BACK = .4
MAX_YAW = .5
PROVENANCE = 'gazebo-hailo-shadow-v1'


def finite(v):
    return type(v) in (int, float) and math.isfinite(v)


def validate(s, target_id, accepted, age_ms, progress_ms, selection_gen):
    """Return (command, reason); independently reject rather than clamp bad data."""
    if not accepted:
        return None, 'Onboard rehearsal not accepted • use --accept-onboard-in-simulation'
    if s.get('kind') != 'onboard' or s.get('paused') or s.get('error'):
        return None, 'Onboard source inactive'
    if not all(finite(v) and 0 <= v <= MAX_AGE_MS for v in (age_ms, progress_ms)):
        return None, 'Onboard command stale • reselect target'
    stats=s.get('stats', {});caps=stats.get('capabilities', {});frame=s.get('frame', {});o=s.get('onboard')
    if caps.get('mount') != 'vehicle' or caps.get('camera') != 'gazebo':
        return None, 'Onboard rehearsal requires a Gazebo vehicle camera'
    if s.get('onboard_provenance') != PROVENANCE or s.get('detector', {}).get('engine') != 'hailo10h-rpc':
        return None, 'Pi Hailo shadow provenance missing'
    if not isinstance(o, dict) or o.get('transmit') is not False or o.get('valid') is not True:
        return None, 'Pi shadow command invalid • hold'
    if (type(frame.get('seq')) is not int or frame['seq'] < 0 or not isinstance(frame.get('epoch'), str) or not frame['epoch']
            or not isinstance(stats.get('instance'), str) or not stats['instance']
            or o.get('frame_seq') != frame['seq'] or o.get('frame_epoch') != frame['epoch']
            or o.get('instance') != stats['instance']):
        return None, 'Onboard frame or process mismatch'
    if type(o.get('selection_gen')) is not int or o['selection_gen']!=selection_gen:
        return None, 'Onboard selection changed • waiting for new command'
    if type(target_id) is not int or type(o.get('target_id')) is not int or o['target_id'] != target_id:
        return None, 'Onboard command is for another target'
    t=next((t for t in s.get('tracks', []) if t.get('id') == target_id), None)
    if not t or t.get('label') != 'person' or t.get('coasting') or not finite(t.get('conf')) or t['conf'] < .5:
        return None, 'Selected person not observed'
    identity=stats.get('identity')
    if identity is not None and (not isinstance(identity, dict) or identity.get('confirmed') is not True
        or identity.get('state') != 'tracking' or identity.get('target') != target_id
        or identity.get('frame_seq') != frame['seq'] or identity.get('frame_epoch') != frame['epoch']
        or identity.get('instance') != stats['instance']):
        return None, 'Identity does not confirm this frame'
    forward,yaw,vertical=(o.get(k) for k in ('forward_vel','yaw_rate','vertical_vel'))
    if not all(finite(v) for v in (forward,yaw,vertical)) or not -MAX_BACK <= forward <= MAX_FORWARD or abs(yaw)>MAX_YAW or vertical != 0:
        return None, 'Onboard command exceeds consumer limits'
    if o.get('range_source') != 'box_height' or o.get('range_m') is not None:
        return None, 'Rehearsal accepts box-height only • no synthetic stereo'
    return o, 'Pi shadow command • mono box-height simulation'


def horizontal(command, quaternion, position, home, speed_limit, travel_limit):
    """Body x to horizontal NED. Never follows pitch into a climb/descent."""
    if (not isinstance(quaternion, (list,tuple)) or len(quaternion)!=4
        or not all(finite(v) for v in quaternion) or abs(sum(v*v for v in quaternion)-1)>.01):
        return None, 'Attitude unavailable'
    w,x,y,z=quaternion
    # This controller assumes a near-level forward-facing camera.
    if 1-2*(x*x+y*y) < math.cos(math.radians(20)):
        return None, 'Vehicle tilt exceeds rehearsal envelope'
    if len(position)<2 or not all(finite(v) for v in (*position[:2],*home,speed_limit,travel_limit)):
        return None, 'Invalid position or limits'
    yaw=math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
    forward=max(-speed_limit,min(speed_limit,command['forward_vel']))
    vn,ve=forward*math.cos(yaw),forward*math.sin(yaw)
    pn,pe=position[0]-home[0],position[1]-home[1]
    if math.hypot(pn,pe)>=travel_limit-.25 and vn*pn+ve*pe>0:
        return None, 'Home travel boundary • hold'
    return (vn,ve,command['yaw_rate']), None
