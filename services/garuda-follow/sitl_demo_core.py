"""Pure behavior logic for the isolated PC demo. Positions are PX4 local NED.

Source kind `fixed_stereo`: the selected person's position comes from the real bench
camera's stereo observation (garuda.fixed-stereo-observation.1). The depth is an unvalidated
preview and the camera's place in the simulated world is ASSUMED, not measured; both are
accepted only with the explicit simulation switch `accept_preview`, and both stay labelled.

The booth-to-world target mapping is illustrative, not metric camera localization.
"""
import math
import mono_estimate
import onboard_source

def similarity(a,b):
    if not isinstance(a,list) or not isinstance(b,list) or len(a)!=128 or len(b)!=128:return 0.
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in a+b):return 0.
    aa=sum(x*x for x in a);bb=sum(x*x for x in b)
    return sum(x*y for x,y in zip(a,b))/math.sqrt(aa*bb) if aa>0 and bb>0 else 0.


class DemoControl:
    def __init__(self):
        self.selection_generation=0
        self.target_id=None; self.epoch=None; self.instance=None; self.sequence=None; self.progress=-math.inf
        self.snapshot={}; self.received=-math.inf; self.gesture_key=None
        self.gestures=False; self.mode='hold'; self.gap=2.; self.speed=1.; self.limit=10.
        self.angle=None; self.target=None; self.reason='Select a target'
        self.gesture_since=None;self.gesture_sent=False;self.gesture_pending=None
        self.confirm_s=1.2;self.direction=1;self.acceleration=.8;self.last_velocity=(0.,0.)
        self.home=(0.,0.);self.lock_state='UNSELECTED';self.command_yaw=None
        self.profiles=[];self.excluded=set();self.last_seen=-math.inf;self.last_remembered=None
        self.candidate=None;self.reacquired=False;self.requires_reselection=False
        # fixed_stereo only. Off by default: without it an unqualified preview never moves the vehicle.
        self.accept_preview=False
        self.accept_mono=False;self.accept_onboard=False;self.command_yaw_rate=0.
        # ASSUMED virtual mount: camera 0.8 m behind home, level, looking along local north.
        # Not a measurement; replaced by a measured mount transform when calibration exists.
        self.camera=dict(assumed=True,behind_home_m=.8,east_m=0.,yaw_rad=0.)
        self.stereo_seen=-math.inf;self.stereo_reason=''
        # fixed_stereo only. Off: any loss longer than 0.5 s ends the selection until the operator
        # selects again. On: the same conservative clothing-appearance return match the Pi source
        # already uses may move the selection to the person's new track ID (walking out of the
        # tracker's grasp changes the ID); the camera is then asked to measure that ID.
        self.reacquire=False

    def select(self, tid):
        self.selection_generation+=1;self.boundary_reason=''
        self.target_id=tid; self.epoch=self.snapshot.get('frame',{}).get('epoch')
        # The detector process is part of who was selected: track IDs and frame numbers mean
        # nothing across a detector restart, even when the camera's own run continues.
        self.instance=self.snapshot.get('stats',{}).get('instance')
        self.angle=None; self.gesture_key=None; self.mode='hold'
        self.gesture_since=None;self.gesture_sent=False;self.gesture_pending=None
        self.target=None;self.last_velocity=(0.,0.)
        self.profiles=[];self.excluded=set();self.last_seen=-math.inf;self.last_remembered=None
        self.candidate=None;self.reacquired=False;self.requires_reselection=False
        self.stereo_seen=-math.inf

    def boundary(self, why):
        """The producer's identities are not continuous across this point. Nothing learned before
        it may carry the selection over, with or without automatic re-acquire."""
        self.selection_generation+=1
        self.requires_reselection=True;self.profiles=[];self.excluded=set();self.candidate=None
        self.reacquired=False;self.target=None;self.last_velocity=(0.,0.);self.boundary_reason=why

    def reset(self):
        self.select(None);self.snapshot={};self.sequence=None;self.epoch=None;self.instance=None
        self.received=self.progress=-math.inf;self.lock_state='UNSELECTED'

    def receive(self, data, now):
        if now-self.progress>.8:self.gesture_key=None;self.gesture_since=None;self.gesture_sent=False
        self.snapshot=data;self.received=now
        frame=data.get('frame',{});key=(frame.get('epoch'),frame.get('seq'))
        if key==self.sequence:return None
        # A frame number that goes backwards within one camera run is not progress: the producer
        # restarted or replayed. With a selection, that ends it until the operator selects again.
        old=self.sequence
        if self.target_id is not None and old is not None and old[0]==key[0] and type(old[1]) is int and type(key[1]) is int and key[1]<old[1]:
            self.boundary('Frame sequence went backwards')
        self.sequence=key;self.progress=now
        if not self.gestures:
            self.gesture_pending=None;self.gesture_key=None;self.gesture_since=None;return None
        t=self.track(now)
        name=t.get('gesture',{}).get('name') if t else None
        if name!=self.gesture_key:
            self.gesture_key=name;self.gesture_since=now;self.gesture_sent=False
        command={'follow_me':'takeoff_follow','stop':'hold','land':'land','circle':'circle'}.get(name)
        if not command:self.gesture_pending=None;return None
        remaining=max(0.,self.confirm_s-(now-self.gesture_since))
        self.gesture_pending=dict(name=name,remaining_s=remaining,confirmed=self.gesture_sent)
        if (name=='stop' or remaining==0) and not self.gesture_sent:
            self.gesture_sent=True;self.gesture_pending['confirmed']=True;return command
        return None

    def track(self, now):
        s=self.snapshot;age=s.get('age_ms')
        if self.target_id is None:self.lock_state='UNSELECTED';self.reason='Select your person ID';return None
        if self.requires_reselection:
            self.lock_state='RESELECT';self.reason=(getattr(self,'boundary_reason','') or 'Observation continuity lost')+' • select target again';return None
        if now-self.received>.8 or now-self.progress>.8 or not isinstance(age,(float,int)) or not math.isfinite(age) or age+(now-self.received)*1000>700:
            self.lock_state='STALE';self.gesture_pending=None;self.gesture_key=None;self.gesture_since=None;self.candidate=None
            if s.get('kind') in ('gazebo','fixed_stereo','mono_estimate','onboard'):
                self.requires_reselection=True;self.profiles=[];self.target=None
            self.reason='Perception stale • hold';return None
        if self.epoch!=s.get('frame',{}).get('epoch'):
            self.requires_reselection=True;self.profiles=[];self.target=None
            self.lock_state='RESELECT';self.reason='Camera restarted • select target again';return None
        if self.instance!=s.get('stats',{}).get('instance'):
            self.boundary('Detector restarted')
            self.lock_state='RESELECT';self.reason='Detector restarted • select target again';return None
        if s.get('kind','pi') in ('pi','fixed_stereo','mono_estimate') and s.get('stats',{}).get('capabilities',{}).get('mount')=='vehicle':
            self.target=None;self.lock_state='LOST';self.reason='Camera is on the vehicle • fixed-camera mapping refused';return None
        if s.get('kind')=='onboard':
            if not onboard_source.finite(age):
                self.lock_state='STALE';self.reason='Invalid onboard age';return None
            o,why=onboard_source.validate(s,self.target_id,self.accept_onboard,
                age+(now-self.received)*1000,(now-self.progress)*1000,self.selection_generation)
            self.target=None;self.reason=why
            if o is None:
                self.lock_state='LOST'
                if ('stale' in why or why in ('Onboard command is for another target',
                        'Identity does not confirm this frame')):
                    self.boundary(why)
                return None
            self.lock_state='LOCKED'
            return next(t for t in s['tracks'] if t['id']==self.target_id)
        mono=s.get('kind')=='mono_estimate'
        stereo=s.get('kind')=='fixed_stereo'
        # The camera may keep the identity itself (learned re-ID on the Pi). With re-acquire
        # allowed, its decision to move the selection to a new track ID is adopted here, and
        # this controller's own colour-based return match is not used alongside it.
        identity=s.get('stats',{}).get('identity') if stereo or mono else None
        if not isinstance(identity,dict):identity=None
        camera_keeps_identity=identity is not None      # then no colour-based guess is made here, whatever this frame's block says
        # The decision must belong to exactly this frame of this detector process.
        if identity and (identity.get('instance')!=self.instance or identity.get('frame_seq')!=s.get('frame',{}).get('seq') or identity.get('frame_epoch')!=s.get('frame',{}).get('epoch')):identity=None
        if mono and camera_keeps_identity and (identity is None or identity.get('target')!=self.target_id):
            self.target=None;self.lock_state='LOST';self.reason='Mono identity does not confirm this selected frame • hold';return None
        if identity and stereo and self.reacquire and identity.get('rebound_from')==self.target_id and type(identity.get('target')) is int and identity.get('confirmed') is True:
            self.target_id=identity['target'];self.reacquired=True;self.angle=None;self.last_velocity=(0.,0.)
        if identity and identity.get('target')==self.target_id and identity.get('confirmed') is not True:
            # The camera is not sure this track is still the selected person: no position, hold.
            self.target=None;self.lock_state='LOST'
            state=identity.get('state')
            if state=='ambiguous':self.reason='Two people look alike • select again'
            elif state in ('expired','no_reference'):self.reason='Identity lost • select again'
            else:self.reason='Camera is checking that this is still the selected person • hold'
            return None
        t=next((t for t in s.get('tracks',[]) if t['id']==self.target_id),None)
        if mono or (stereo and not self.reacquire):
            # No appearance re-match here: a gap longer than the continuity limit ends the
            # selection and the operator selects again. Nothing resumes by itself.
            seen=bool(t) and not t.get('coasting') and t.get('conf',0)>=.5
            if seen:self.stereo_seen=now
            elif self.stereo_seen>-math.inf and now-self.stereo_seen>.5:
                self.requires_reselection=True;self.target=None
                self.lock_state='RESELECT';self.reason='Target unseen for more than 0.5 s • select target again';return None
            if not seen:
                self.target=None
                self.lock_state='LOST';self.reason='Target not visible • hold';return None
            # The position is worked out for every observation, flying or not, so the display
            # and the Gazebo figure show where the camera puts the person before take-off too.
            if mono:
                self.target,self.stereo_reason=mono_estimate.target(s,t,self.home,self.accept_mono)
            else:self.target=self.stereo_target(t)
            self.lock_state='LOCKED'
            self.reason=(self.stereo_reason if mono else 'Target locked • stereo position (unvalidated, assumed camera pose)') if self.target else self.stereo_reason
            return t
        if not t or t.get('coasting'):
            t=None if camera_keeps_identity else self.return_match(now)
        if not t or t.get('conf',0)<.5:
            if stereo:self.target=None
            self.lock_state='LOST';self.reason='Target lost • hold; click to confirm any new ID';return None
        frame_seq=s.get('frame',{}).get('seq')
        if frame_seq!=self.last_remembered:
            self.last_remembered=frame_seq;self.last_seen=now
            self.excluded.update(other['id'] for other in s.get('tracks',[]) if other['id']!=t['id'] and not other.get('coasting'))
            profile=t.get('appearance')
            if similarity(profile,profile)>.99 and (not self.profiles or similarity(self.profiles[0],profile)>.88):
                self.profiles.append(profile)
                if len(self.profiles)>5:self.profiles.pop(1)
        self.candidate=None
        self.lock_state='LOCKED'
        if stereo:
            self.target=self.stereo_target(t)
            self.reason=('Target locked • stereo position (unvalidated, assumed camera pose)'+(' • appearance return matched' if self.reacquired else '')) if self.target else self.stereo_reason
        return t

    def return_match(self,now):
        # Same conservative clothing-appearance criteria as the preserved browser demo.
        # No face identity claim; uncertain returns require an operator click.
        if not self.profiles or now-self.last_seen>30:self.candidate=None;return None
        candidates=[]
        for t in self.snapshot.get('tracks',[]):
            if t['id']==self.target_id or t.get('coasting') or t.get('conf',0)<.5:continue
            candidates.append((max(similarity(p,t.get('appearance')) for p in self.profiles),t))
        candidates.sort(key=lambda p:p[0],reverse=True)
        if not candidates:self.candidate=None;return None
        score,t=candidates[0]
        if score<.9 or t['id'] in self.excluded:self.candidate=None;return None
        if len(candidates)>1 and score-candidates[1][0]<.1:
            self.candidate=dict(id=t['id'],score=score,ambiguous=True);return None
        seq=self.snapshot.get('frame',{}).get('seq')
        if not self.candidate or self.candidate.get('id')!=t['id'] or self.candidate.get('ambiguous'):
            self.candidate=dict(id=t['id'],score=score,since=now,seq=seq,frames=1,ambiguous=False)
        elif self.candidate['seq']!=seq:
            self.candidate.update(seq=seq,frames=self.candidate['frames']+1,score=score)
        if now-self.candidate['since']<.8 or self.candidate['frames']<4:return None
        self.target_id=t['id'];self.reacquired=True;self.angle=None;self.last_velocity=(0.,0.)
        return t

    def stereo_point(self, t):
        """Optical point (right, down, forward) of the selected person from the fixed-camera
        observation attached to the current snapshot, or None with self.stereo_reason set.
        Nothing is remembered: an observation without a point is no position."""
        s=self.snapshot;o=s.get('fixed_stereo');f=s.get('frame',{})
        if s.get('stats',{}).get('capabilities',{}).get('mount')=='vehicle':
            self.stereo_reason='Camera is on the vehicle • fixed-camera mapping refused';return None
        def no(reason):self.stereo_reason=reason;return None
        if not isinstance(o,dict) or o.get('schema')!='garuda.fixed-stereo-observation.1' or o.get('source')!='fixed_stereo':
            return no('Stereo observation missing • hold')
        if o.get('frame_epoch')!=f.get('epoch') or o.get('frame_seq')!=f.get('seq'):
            return no('Stereo observation is for another frame • hold')
        if o.get('source_instance') is None or o.get('source_instance')!=s.get('stats',{}).get('instance') or o['source_instance']!=self.instance:
            return no('Stereo observation is from another detector process • select again')
        if o.get('target_id')!=t.get('id'):
            return no('Camera is measuring another person • select here again')
        if o.get('measurement_qualified') is not True or o.get('preview_only') is not False:
            if not self.accept_preview:
                return no('Unvalidated stereo preview not accepted • restart with --accept-preview-stereo-in-simulation')
        p=o.get('optical_point_m')
        if o.get('status')!='ok' or not isinstance(p,list) or len(p)!=3 or not all(type(v) in (int,float) and math.isfinite(v) for v in p) or not .3<p[2]<30:
            return no('No stereo distance ('+str(o.get('reason'))+') • hold')
        self.stereo_reason='';return p

    def stereo_target(self, t):
        """(north, east) of the selected person in PX4 local NED from the stereo point and the
        ASSUMED camera pose, or None (self.stereo_reason says why)."""
        point=self.stereo_point(t)
        if point is None:return None
        right,_down,forward=point
        c=self.camera;cn=self.home[0]-c['behind_home_m'];ce=self.home[1]+c['east_m'];yaw=c['yaw_rad']
        return (cn+math.cos(yaw)*forward-math.sin(yaw)*right,ce+math.sin(yaw)*forward+math.cos(yaw)*right)

    def stop(self):
        self.last_velocity=(0.,0.);self.command_yaw=None;self.command_yaw_rate=0.;return (0.,0.,None)

    def velocity(self, position, now, dt, quaternion=None, observation_pose=None):
        self.command_yaw_rate=0.
        if self.mode=='hold':self.reason='Holding position';return self.stop()
        t=self.track(now)
        if t is None:return self.stop()
        frame=self.snapshot['frame'];w=frame.get('width',0)
        if w<=0:self.reason='Invalid camera dimensions';return (0.,0.,None)
        kind=self.snapshot.get('kind','pi')
        if kind=='replay':self.reason='Replay analysis only • flight disabled';return self.stop()
        if kind=='onboard':
            if self.mode!='follow':self.reason='Onboard rehearsal supports Follow and Hold only';return self.stop()
            move,why=onboard_source.horizontal(self.snapshot['onboard'],quaternion,position,self.home,self.speed,self.limit)
            if move is None:self.reason=why;return self.stop()
            vn,ve,self.command_yaw_rate=move
            self.last_velocity=(vn,ve)
            return vn,ve,None
        if kind=='gazebo':
            if observation_pose is None:
                self.target=None;self.reason='Observation pose unavailable • hold';return self.stop()
            camera_position,camera_quaternion=observation_pose
            if len(camera_position)!=3 or len(camera_quaternion)!=4 or not all(math.isfinite(v) for v in [*camera_position,*camera_quaternion]):
                self.target=None;self.reason='Invalid observation pose • hold';return self.stop()
            distance=t.get('range_m');camera=self.snapshot.get('camera',{})
            if not camera.get('depth_paired') or not isinstance(distance,(float,int)) or not math.isfinite(distance) or not .3<distance<30:
                self.reason='No paired depth • hold';return self.stop()
            if quaternion is None or len(quaternion)!=4 or not all(math.isfinite(v) for v in quaternion):
                self.reason='Attitude unavailable • hold';return self.stop()
            qw,qx,qy,qz=camera_quaternion
            focal=w/(2*math.tan(camera.get('hfov',1.2)/2))
            forward=distance;right=((t['x']+t['w']/2)-w/2)*distance/focal
            down=((t['y']+t['h']*.425)-frame['height']/2)*distance/focal
            pitch=camera.get('pitch',.12)
            bx=math.cos(pitch)*forward-math.sin(pitch)*down+.15
            by=right;bz=math.sin(pitch)*forward+math.cos(pitch)*down-.18
            north=camera_position[0]+(1-2*(qy*qy+qz*qz))*bx+2*(qx*qy-qw*qz)*by+2*(qx*qz+qw*qy)*bz
            east=camera_position[1]+2*(qx*qy+qw*qz)*bx+(1-2*(qx*qx+qz*qz))*by+2*(qy*qz-qw*qx)*bz
        elif kind in ('fixed_stereo','mono_estimate'):
            if self.target is None:self.reason=self.stereo_reason or 'No stereo position • hold';return self.stop()
            north,east=self.target
        else:
            # Booth camera is explicitly schematic; it is not onboard localization.
            height=frame.get('height',720)
            if height<=0 or t.get('h',0)<=0:self.reason='Invalid person size • hold';return self.stop()
            # Port of Garuda.project from the preserved demo: box size drives range,
            # horizontal centre drives bearing. This remains explicitly schematic.
            depth=max(1.8,min(7.,5.5-t['h']/height*4))
            north=self.home[0]+depth-.8
            east=self.home[1]+((t['x']+t['w']/2)/w-.5)*7
        self.target=(north,east)
        radius=min(self.gap,max(0.,self.limit-math.hypot(north-self.home[0],east-self.home[1])-.2))
        if kind=='gazebo':
            dn,de=position[0]-north,position[1]-east;distance=max(.1,math.hypot(dn,de))
            radial=(self.gap-distance)*.8
            tangent=self.direction*self.speed*.65 if self.mode=='circle' else 0.
            if self.mode=='circle' and radius<self.gap-.1:
                self.reason='Orbit would cross travel boundary • hold';return self.stop()
            vn=radial*dn/distance-tangent*de/distance
            ve=radial*de/distance+tangent*dn/distance
            self.reason='Following onboard camera target' if self.mode=='follow' else 'Orbiting onboard camera target'
            return self.bounded(vn,ve,position,math.atan2(east-position[1],north-position[0]),dt,quaternion)
        if self.mode=='circle':
            if radius<.5:self.reason='Target too close to travel boundary';return (0.,0.,None)
            if self.angle is None:self.angle=math.atan2(position[1]-east,position[0]-north)
            goal=(north+radius*math.cos(self.angle),east+radius*math.sin(self.angle))
            if math.hypot(goal[0]-position[0],goal[1]-position[1])<.5:self.angle+=self.direction*min(.5,self.speed*.65/radius)*dt
            self.reason='Orbiting virtual target from live booth tracking'
        else:
            # Standoff lies on the ray from the booth camera to the person, matching
            # the original illustration instead of an arbitrary constant north offset.
            cn,ce=self.home[0]-.8,self.home[1];dn,de=north-cn,east-ce;distance=max(.01,math.hypot(dn,de))
            # With a metric stereo position the requested gap is honoured (the vehicle may stand
            # off behind the camera); the schematic view keeps it between camera and person.
            offset=self.gap if kind=='fixed_stereo' else min(self.gap,max(0.,distance-.8))
            goal=(north-offset*dn/distance,east-offset*de/distance)
            self.reason=('Following fixed-camera stereo target • unvalidated depth, assumed camera pose' if kind=='fixed_stereo' else ('Following mono estimate • schematic mapping, not measured distance' if kind=='mono_estimate' else 'Following live camera target'))+(' • appearance return matched' if self.reacquired else '')
        vn,ve=(goal[0]-position[0])*1.4,(goal[1]-position[1])*1.4
        return self.bounded(vn,ve,position,math.atan2(east-position[1],north-position[0]),dt,quaternion)

    def bounded(self,vn,ve,position,yaw,dt,quaternion):
        speed=math.hypot(vn,ve)
        if speed>self.speed:vn*=self.speed/speed;ve*=self.speed/speed
        pn,pe=position[0]-self.home[0],position[1]-self.home[1]
        dist=math.hypot(pn,pe)
        if dist>=self.limit-.25 and vn*pn+ve*pe>0:
            self.reason='Home travel boundary • hold';return self.stop()
        oldn,olde=self.last_velocity;delta=math.hypot(vn-oldn,ve-olde)
        cap=self.acceleration*min(.1,max(0.,dt))
        if delta>cap:vn=oldn+(vn-oldn)*cap/delta;ve=olde+(ve-olde)*cap/delta
        self.last_velocity=(vn,ve)
        if quaternion:
            w,x,y,z=quaternion;heading=math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
            if self.command_yaw is None:self.command_yaw=heading
            error=(yaw-self.command_yaw+math.pi)%(2*math.pi)-math.pi
            self.command_yaw+=max(-.6*dt,min(.6*dt,error))
            yaw=self.command_yaw
        return vn,ve,yaw
