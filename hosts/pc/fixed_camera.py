"""Read-only fixed-camera measurement projection. Never emits flight commands."""
import math,time

MAX_AGE_MS=700

def finite(x):return type(x) in (int,float) and math.isfinite(x)
def valid_pose(c):
 p=c.get('position_m');r=c.get('world_from_optical')
 if not isinstance(p,list) or len(p)!=3 or not all(finite(v) for v in p):return False
 if not isinstance(r,list) or len(r)!=3 or any(not isinstance(v,list) or len(v)!=3 or not all(finite(x) for x in v) for v in r):return False
 if any(abs(sum(r[i][k]*r[j][k] for k in range(3))-(i==j))>1e-6 for i in range(3) for j in range(3)):return False
 det=sum(r[0][i]*(r[1][(i+1)%3]*r[2][(i+2)%3]-r[1][(i+2)%3]*r[2][(i+1)%3]) for i in range(3))
 return abs(det-1)<1e-6

class FixedCamera:
 def __init__(self,calibration=None):
  self.calibration=calibration or {};self.selected=None;self.last_key=None;self.progress=None;self.requires_reselection=False
 def select(self,s,tid,epoch):
  f=s.get('frame',{});age=s.get('age_ms')
  if not finite(age) or not 0<=age<MAX_AGE_MS or epoch!=f.get('epoch'):raise ValueError('Camera frame is stale or changed')
  if s.get('lock',{}).get('id')!=tid:raise ValueError('Select this person in the camera page first')
  if not any(t.get('id')==tid and t.get('label')=='person' and not t.get('coasting') and finite(t.get('conf')) and .5<=t['conf']<=1 for t in s.get('tracks',[])):raise ValueError('A visible person must be selected')
  self.selected=(epoch,tid);self.requires_reselection=False;self.last_key=None;self.progress=None
 def invalidate(self):
  if self.selected is not None:self.requires_reselection=True
 def evaluate(self,s,sim,now=None):
  now=time.monotonic() if now is None else now
  out=dict(status='unavailable',reason='',position_m=None,optical_point_m=None,selected=self.selected,requires_reselection=self.requires_reselection,flight_enabled=False)
  def fail(reason,latch=False):
   if latch:self.invalidate()
   out.update(reason=reason,requires_reselection=self.requires_reselection);return out
  if sim.get('armed') is not False or sim.get('landed') is not True or sim.get('telemetry_fresh') is not True:return fail('Simulator must be grounded with fresh telemetry',True)
  age=s.get('age_ms')
  if not finite(age) or not 0<=age<MAX_AGE_MS:return fail('Camera data is stale',True)
  f=s.get('frame',{});key=(f.get('epoch'),f.get('seq'))
  if not key[0] or type(key[1]) is not int:return fail('Frame identity missing',True)
  if self.last_key is not None and (key[0]!=self.last_key[0] or key[1]<self.last_key[1]):self.invalidate()
  if self.progress is not None and (now<self.progress or now-self.progress>.7):self.invalidate()
  if key!=self.last_key:self.last_key=key;self.progress=now
  if self.selected is None:return fail('Confirm the camera’s selected person')
  epoch,tid=self.selected
  if self.requires_reselection or key[0]!=epoch:return fail('Select the person again after the gap or restart',True)
  t=next((t for t in s.get('tracks',[]) if t.get('id')==tid),None)
  if not t or t.get('label')!='person' or t.get('coasting') or not finite(t.get('conf')) or not .5<=t['conf']<=1 or s.get('lock',{}).get('id')!=tid:return fail('Selected person lost or changed — reselect',True)
  b=s.get('stereo')
  if not isinstance(b,dict):return fail('No qualified person-depth result for Gazebo; stereo preview is separate')
  if b.get('measurement_qualified') is not True or b.get('preview_only') is True:return fail('Depth is unqualified; preview cannot position a measured marker')
  if b.get('status')!='ok' or not finite(b.get('z_m')) or not .3<b['z_m']<30:return fail('No valid depth for the selected person')
  pair=b.get('pair',{});capture=f.get('capture_pair',{})
  if not pair.get('epoch') or type(pair.get('rgb_seq')) is not int or any(pair.get(k)!=capture.get(k) for k in ('epoch','rgb_seq')):return fail('Depth does not match this image')
  g=b.get('geometry') or {};c=self.calibration
  if b.get('depth_reference_frame')!='rgb_optical' or g.get('verified') is not True or g.get('reference_frame')!='rgb_optical' or g.get('distortion')!='none':return fail('Delivered-image geometry and depth axis need verification')
  if g.get('epoch')!=pair['epoch'] or not g.get('calibration_id') or g.get('calibration_id')!=b.get('calibration_id'):return fail('Geometry identity mismatch')
  if c.get('mount_verified') is not True or not c.get('evidence_ref') or c.get('calibration_id')!=g['calibration_id'] or not valid_pose(c):return fail('Measured fixed-camera pose and calibration evidence are required')
  if any(type(f.get(k)) is not int or f[k]<=0 for k in ('width','height')):return fail('Invalid image dimensions')
  if g.get('width')!=f.get('width') or g.get('height')!=f.get('height'):return fail('Image dimensions do not match calibration')
  if not all(finite(g.get(k)) for k in ('fx','fy','cx','cy')) or min(g['fx'],g['fy'])<=0:return fail('Invalid camera intrinsics')
  if not 0<=g['cx']<f['width'] or not 0<=g['cy']<f['height']:return fail('Invalid principal point')
  roi=b.get('roi')
  if not isinstance(roi,(list,tuple)) or len(roi)!=4 or not all(finite(v) for v in roi):return fail('Measurement region missing')
  x0,y0,x1,y1=roi
  if not 0<=x0<x1<=f['width'] or not 0<=y0<y1<=f['height']:return fail('Invalid measurement region')
  z=b['z_m'];q=[((x0+x1)/2-g['cx'])*z/g['fx'],((y0+y1)/2-g['cy'])*z/g['fy'],z]
  p=[c['position_m'][i]+sum(c['world_from_optical'][i][j]*q[j] for j in range(3)) for i in range(3)]
  if not all(finite(v) for v in q+p):return fail('Nonfinite projected position')
  out.update(status='ok',reason='Measured torso-region marker · grounded diagnostic only',position_m=p,optical_point_m=q,calibration_id=g['calibration_id']);return out
