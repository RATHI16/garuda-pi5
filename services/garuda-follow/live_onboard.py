"""Live Pi body commands into isolated SITL, explicitly open loop.

No publisher or FC transport lives here. A request generation is a one-shot grant:
startup, restart or any failure requires a later operator request. Pi clocks are
never compared with PC clocks; the reader supplies conservative observation age.
"""
import math
import threading
import time
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from onboard_source import finite, horizontal


def validate(snapshot, age_ms, progress_ms):
    if not all(finite(v) and 0 <= v <= 400 for v in (age_ms, progress_ms)):
        return None, 'Live command stale'
    if snapshot.get('error') or snapshot.get('paused'):
        return None, 'Live source unavailable'
    stats=snapshot.get('stats',{});caps=stats.get('capabilities',{});frame=snapshot.get('frame',{})
    o=snapshot.get('onboard');lock=snapshot.get('lock',{})
    if caps.get('mount')!='vehicle' or caps.get('camera') not in ('oak','cm3','usb'):
        return None, 'Select vehicle mount on the camera'
    if not isinstance(o,dict) or o.get('valid') is not True or o.get('transmit') is not False:
        return None, 'Pi command invalid'
    if (type(frame.get('seq')) is not int or frame['seq']<0 or not isinstance(frame.get('epoch'),str) or not frame['epoch']
        or not isinstance(stats.get('instance'),str) or not stats['instance']
        or o.get('frame_seq')!=frame['seq'] or o.get('frame_epoch')!=frame['epoch'] or o.get('instance')!=stats['instance']):
        return None, 'Command frame or detector mismatch'
    tid=o.get('target_id')
    if type(tid) is not int or lock.get('id')!=tid or lock.get('coasting') is not False:
        return None, 'Camera selection mismatch'
    t=next((t for t in snapshot.get('tracks',[]) if t.get('id')==tid),None)
    if not t or t.get('label')!='person' or t.get('coasting') is not False or not finite(t.get('conf')) or t['conf']<.5:
        return None, 'Selected person not observed confidently'
    identity=stats.get('identity')
    if identity is not None and (not isinstance(identity,dict) or identity.get('confirmed') is not True
        or identity.get('state')!='tracking' or identity.get('target')!=tid
        or identity.get('frame_seq')!=frame['seq'] or identity.get('frame_epoch')!=frame['epoch']
        or identity.get('instance')!=stats['instance']):
        return None, 'Identity does not confirm this frame'
    f,y,z=(o.get(k) for k in ('forward_vel','yaw_rate','vertical_vel'))
    if not all(finite(v) for v in (f,y,z)) or not -.4<=f<=.8 or abs(y)>.5 or z!=0:
        return None, 'Command exceeds independent limits'
    expected='stereo' if caps.get('distance_source')=='stereo' else 'box_height' if caps['camera'] in ('cm3','usb') else 'none'
    if o.get('range_source')!=expected:return None,'Distance source changed or mismatched'
    distance=o.get('range_m')
    if expected=='stereo':
        if distance is None:
            if f!=0:return None,'Forward motion without stereo distance'
        elif not finite(distance) or not .8<=distance<=12:return None,'Invalid stereo distance'
    elif distance is not None or (expected=='none' and f!=0):return None,'Invalid proxy distance'
    return dict(o),None


class LiveGate:
    def __init__(self):self.reset()
    def reset(self):
        self.observed=None;self.grant=None;self.pin=None;self.command=None;self.request=None
        self.state='ready';self.reason='Choose Nowhere, then PX4 on the PC on the camera page'
        self.was_sending=False;self.sequence=None
    def drop(self,reason):
        self.grant=None;self.command=None;self.was_sending=False
        self.state='holding';self.reason=reason+'; choose Nowhere then PX4 on the PC again'
    def evaluate(self,snapshot,age_ms,progress_ms,accepted):
        self.command=None
        if not accepted:self.drop('Live Pi commands not accepted');return None
        stats=snapshot.get('stats',{});tx=snapshot.get('transmit',{})
        instance=stats.get('instance');gen=tx.get('generation')
        if not isinstance(instance,str) or not instance or tx.get('instance')!=instance or type(gen) is not int or gen<0:
            self.drop('Missing detector/request identity');return None
        key=(instance,gen);self.request=tx.get('request')
        if self.observed is None or instance!=self.observed[0] or gen<self.observed[1]:
            self.observed=key;self.drop('Sender or detector restarted');return None
        new=gen>self.observed[1];self.observed=key
        if tx.get('request')!='sim':
            self.drop('Sending to PC is off');return None
        if new:
            self.grant=key;self.pin=None;self.was_sending=False;self.sequence=None
        if self.grant!=key:return None
        o,why=validate(snapshot,age_ms,progress_ms)
        if o is None:self.drop(why);return None
        pin=(o['frame_epoch'],o['target_id'],o['range_source'])
        if self.pin is not None and pin!=self.pin:self.drop('Target, camera epoch or distance source changed');return None
        if self.sequence is not None and o['frame_seq']<self.sequence:self.drop('Frame sequence regressed');return None
        self.sequence=o['frame_seq'];self.pin=pin;self.command=o;self.state='ready';self.reason='Live camera → SITL OPEN LOOP; Take off and Follow explicitly'
        return o
    def report(self,sending=False,reason=None):
        if sending:self.was_sending=True;self.state='sending';self.reason='OPEN LOOP: live camera does not move with simulated vehicle'
        elif reason:self.state='holding';self.reason=reason
        return dict(state=self.state if self.observed else 'ready',reason=self.reason,request=self.request,
                    generation=self.observed[1] if self.observed else None,
                    instance=self.observed[0] if self.observed else None,open_loop=True,
                    target_id=self.pin[1] if self.grant and self.pin else None)


class SenderStatus:
    """Network feedback stays in the reader thread, never the control timer."""
    def __init__(self,opener=urlopen):
        self.lock=threading.Lock();self.opener=opener;self.desired=None;self.last=-math.inf;self.error=None;self.published_at=-math.inf
    def publish(self,value):
        with self.lock:self.desired=dict(value) if value else None;self.published_at=time.monotonic()
    def take_error(self,expected_key=None):
        with self.lock:error=self.error;self.error=None
        if error is None or (expected_key is not None and error[:2]!=expected_key):return None
        return error[2]
    def send(self,url,now=None):
        now=time.monotonic() if now is None else now
        with self.lock:value=dict(self.desired) if self.desired else None
        if value is None or now-self.last<.25:return
        if now-self.published_at>.1:
            value.update(state='holding',reason='Controller tick stale')
            with self.lock:self.error=(value.get('instance'),value.get('generation'),'Controller tick stale')
        self.last=now
        params={k:v for k,v in value.items() if k in ('state','reason','generation','instance') and v is not None}
        params['target']='sim'
        try:
            req=Request(url+'/api/transmit_status?'+urlencode(params),data=b'')
            with self.opener(req,timeout=.4) as r:ack=json.load(r)
            if ack.get('ok') is not True:raise ValueError('sender status refused')
            if value.get('generation') is not None and (ack.get('instance')!=value['instance'] or ack.get('generation')!=value['generation'] or ack.get('request')!=value.get('request','sim')):
                raise ValueError('sender request changed during report')
        except Exception as exc:
            with self.lock:self.error=(value.get('instance'),value.get('generation'),'Sender status unavailable: '+str(exc))
