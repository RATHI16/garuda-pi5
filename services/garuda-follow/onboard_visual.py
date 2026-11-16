"""Display-only live-camera person placement relative to the SIMULATED vehicle.

Camera at simulated vehicle origin, level and forward-facing is an assumption. The real
bench camera does not follow that vehicle. Never fed back to perception or control.
"""
import math
from live_onboard import validate


def project(snapshot, age_ms, progress_ms, position, quaternion, fresh):
    out = dict(valid=False, simulation_only=True, method='onboard-stereo-display-v1',
               reason='No fresh stereo person position', target=None, target_id=None)
    if not fresh:
        return dict(out, reason='PX4 pose is stale')
    command, why = validate(snapshot, age_ms, progress_ms)
    if command is None:
        return dict(out, reason=why)
    st = snapshot.get('stereo') or {}; geom = st.get('geometry') or {}; frame = snapshot.get('frame') or {}
    def num(v): return type(v) in (int, float) and math.isfinite(v)
    if command.get('range_source') != 'stereo' or st.get('status') != 'ok':
        return out
    z = st.get('z_m'); fx = geom.get('fx'); cx = geom.get('cx'); roi = st.get('roi')
    if (not num(z) or not .8 <= z <= 12 or not num(fx) or fx <= 0 or not num(cx)
        or geom.get('width') != frame.get('width') or geom.get('height') != frame.get('height')
        or not geom.get('calibration_id') or geom.get('calibration_id') != st.get('calibration_id')
        or geom.get('epoch') != (st.get('pair') or {}).get('epoch')
        or not isinstance(roi, (list, tuple)) or len(roi) != 4 or not all(num(v) for v in roi)
        or not 0 <= roi[0] < roi[2] <= frame.get('width', 0)
        or not 0 <= roi[1] < roi[3] <= frame.get('height', 0)):
        return out
    if (not isinstance(position, (list, tuple)) or len(position) != 3 or not all(num(v) for v in position)
        or not isinstance(quaternion, (list, tuple)) or len(quaternion) != 4 or not all(num(v) for v in quaternion)):
        return dict(out, reason='PX4 pose unavailable')
    w, x, y, qz = quaternion
    if abs(w*w+x*x+y*y+qz*qz-1) > .02:
        return dict(out, reason='PX4 attitude invalid')
    yaw = math.atan2(2*(w*qz+x*y), 1-2*(y*y+qz*qz))
    right = ((roi[0]+roi[2])/2-cx)*z/fx
    north = position[0] + math.cos(yaw)*z - math.sin(yaw)*right
    east = position[1] + math.sin(yaw)*z + math.cos(yaw)*right
    return dict(out, valid=True, target=[north,east], target_id=command['target_id'],
                frame_epoch=frame['epoch'], frame_seq=frame['seq'],
                reason='Stereo preview; camera assumed level at simulated vehicle origin; display only')
