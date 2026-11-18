/* Deterministic browser-side illustration, not an aircraft flight controller. */
(function(root){
'use strict';
const finite=Number.isFinite;
function similarity(a,b){
  if(!Array.isArray(a)||!Array.isArray(b)||a.length!==128||b.length!==128)return 0;
  let dot=0,aa=0,bb=0;for(let i=0;i<a.length;i++){if(!finite(a[i])||!finite(b[i]))return 0;dot+=a[i]*b[i];aa+=a[i]*a[i];bb+=b[i]*b[i];}
  return aa>0&&bb>0?dot/Math.sqrt(aa*bb):0;
}
// Names a face from the enrolled people. A name is given only for a fresh face that clearly
// matches one person: best score at least .5 and at least .08 above any other person.
function cleanName(name){return String(name??'').replace(/[\u0000-\u001f\u007f]/g,' ').replace(/\s+/g,' ').trim().slice(0,24);}
function validPeople(list){
  if(!Array.isArray(list))return [];
  return list.map(p=>({name:cleanName(p?.name),samples:Array.isArray(p?.samples)?p.samples.filter(e=>similarity(e,e)>.99).slice(0,3):[]})).filter(p=>p.name&&p.samples.length).slice(0,12);
}
function recognise(people,face){
  if(!face||!(face.age_ms<1800)||!Array.isArray(people))return null;
  const scores=people.map(p=>({name:p.name,score:Math.max(...p.samples.map(e=>similarity(e,face.embedding)))})).sort((a,b)=>b.score-a.score);
  const top=scores[0];if(!top||top.score<.5||(scores[1]&&top.score-scores[1].score<.08))return null;
  return top.name;
}
class ReturnMatcher {
  constructor(){this.profiles=[];this.lastSeen=-Infinity;this.lastSeq=null;this.candidate=null;this.excluded=new Set();this.reason='';this.faceRequired=false;this.faceProfiles=[];}
  remember(t,s,now){
    if(s.frame.seq===this.lastSeq)return;this.lastSeq=s.frame.seq;this.lastSeen=now;this.candidate=null;
    for(const other of s.tracks)if(other.id!==t.id&&other.label==='person'&&!other.coasting)this.excluded.add(other.id);
    if(similarity(t.appearance,t.appearance)<.99)return;
    // Anchor + a few nearby lighting/view variants, never train on an unrelated jump.
    if(!this.profiles.length)this.profiles.push(t.appearance);
    else if(similarity(this.profiles[0],t.appearance)>.88){
      this.profiles.push(t.appearance);if(this.profiles.length>5)this.profiles.splice(1,1);
    }
  }
  find(s,now,oldId){
    this.reason='Target out of view • waiting for return';
    if(now-this.lastSeen>30000){this.candidate=null;this.reason='Return window expired • select again';return null;}
    const profiles=this.faceRequired?this.faceProfiles:this.profiles;
    if(!profiles.length){this.candidate=null;this.reason=this.faceRequired?'Enroll the selected face before leaving view':'No appearance reference • select again';return null;}
    const candidates=s.tracks.filter(t=>t.label==='person'&&!t.coasting&&t.conf>=.5&&t.id!==oldId)
      .filter(t=>!this.faceRequired||(t.face&&t.face.age_ms<1800))
      .map(t=>({t,score:Math.max(...profiles.map(p=>similarity(p,this.faceRequired?t.face.embedding:t.appearance)))})).sort((a,b)=>b.score-a.score);
    const top=candidates[0];
    if(!top||top.score<(this.faceRequired ? .50 : .90)||this.excluded.has(top.t.id)){this.candidate=null;if(this.faceRequired)this.reason='Waiting for the enrolled face • look toward camera';return null;}
    if(candidates[1]&&top.score-candidates[1].score<(this.faceRequired ? .08 : .10)){this.candidate=null;this.reason='Similar-looking people • waiting for a clear match';return null;}
    this.reason=this.faceRequired?'Returning face candidate • confirming match':'Returning target candidate • checking appearance';
    const sample=this.faceRequired?top.t.face.sample:s.frame.seq;
    if(this.candidate?.id!==top.t.id)this.candidate={id:top.t.id,since:now,seq:sample,frames:1};
    else if(this.candidate.seq!==sample){this.candidate.seq=sample;this.candidate.frames++;}
    if(now-this.candidate.since<800||this.candidate.frames<(this.faceRequired?2:4))return null;
    return top.t;
  }
}
class Flight {
  airState='GROUNDED';altitude=0;flightStyle='follow';orbitAngle=null;
  takeoff(){if(this.airState!=='GROUNDED')return false;this.airState='TAKING OFF';this.paused=false;this.goodSince=null;return true;}
  land(){if(this.airState==='GROUNDED')return;this.airState='LANDING';this.goodSince=null;this.orbitAngle=null;}
  setFlightStyle(style){if(!['follow','circle'].includes(style))return;this.flightStyle=style;this.orbitAngle=null;this.goodSince=null;}
  constructor(){this.x=0;this.y=0.8;this.vx=0;this.vy=0;this.heading=0;this.mode='IDLE';this.reason='Choose a person to begin';this.id=null;this.epoch=null;this.received=-Infinity;this.progress=-Infinity;this.seq=null;this.goodSince=null;this.lastGoodSeq=null;this.fault=false;this.paused=false;this.requiresSelection=false;this.settings={gap:2,radius:6,speed:2.2};this.side=0;this.gesturesEnabled=false;this.gestureKey='';this.faceEnabled=false;this.enrolling=false;this.faceSamples=[];this.faceMessage='Face matching off';this.people=[];this.named=new Map();this.enrollName='';}
  configure(settings){for(const [key,min,max]of[['gap',.5,5],['radius',1,12],['speed',.25,3]]){const v=Number(settings[key]);if(finite(v))this.settings[key]=Math.max(min,Math.min(max,v));}}
  setFaceMode(enabled){this.faceEnabled=enabled;this.enrolling=false;this.faceSamples=[];if(this.matcher){this.matcher.faceRequired=enabled;this.matcher.faceProfiles=[];}this.faceMessage=enabled?'Select a person, then enroll their face':'Face matching off';}
  enroll(name){name=cleanName(name);if(this.id===null||!this.faceEnabled||!name)return false;this.enrollName=name;this.enrolling=true;this.faceSamples=[];this.enrollSeen=new Set();this.faceMessage='Look toward camera • collecting three face samples';return true;}
  receive(s,now){this.s=s;this.received=now;const seq=s.frame?.seq;if(seq!==this.seq){this.seq=seq;this.progress=now;this.processGestures(s,now);this.processEnrollment(s);this.processNames(s);}}
  processEnrollment(s){
    if(!this.enrolling)return;
    if(this.epoch!==s.frame.epoch){this.enrolling=false;this.faceMessage='Camera changed • select and enroll again';return;}
    const t=s.tracks.find(t=>t.id===this.id&&!t.coasting),face=t?.face;
    if(!face||face.age_ms>=1800||s.age_ms>700||similarity(face.embedding,face.embedding)<.99)return;
    if(this.enrollSeen.has(face.sample))return;this.enrollSeen.add(face.sample);
    if(this.faceSamples.length&&similarity(this.faceSamples[0],face.embedding)<.5){this.faceSamples=[];this.faceMessage='Face samples inconsistent • keep facing camera';return;}
    this.faceSamples.push(face.embedding);this.faceMessage='Enrolling face '+this.faceSamples.length+'/3';
    if(this.faceSamples.length===3){this.matcher.faceProfiles=[...this.faceSamples];this.enrolling=false;this.people=validPeople([...this.people.filter(p=>p.name.toLowerCase()!==this.enrollName.toLowerCase()),{name:this.enrollName,samples:[...this.faceSamples]}]);this.named.set(this.id,this.enrollName);this.faceMessage='Face enrolled as '+this.enrollName;this.onPeople?.(this.people);}
  }
  // A track keeps its name while its face is turned away; a fresh face that is not clearly
  // that person removes it, and a new camera session removes all of them.
  processNames(s){
    const epoch=s.frame?.epoch;if(epoch!==this.nameEpoch){this.nameEpoch=epoch;this.named.clear();}
    if(!this.faceEnabled||!this.people.length){this.named.clear();return;}
    const live=new Set();
    for(const t of s.tracks||[]){live.add(t.id);if(!t.face||!(t.face.age_ms<1800)||t.coasting)continue;const name=recognise(this.people,t.face);if(name)this.named.set(t.id,name);else this.named.delete(t.id);}
    for(const id of this.named.keys())if(!live.has(id))this.named.delete(id);
  }
  nameOf(id){return this.named.get(id)||null;}
  setPeople(list){this.people=validPeople(list);this.named.clear();}
  forgetPeople(){this.people=[];this.named.clear();this.onPeople?.(this.people);}
  processGestures(s,now){
    if(!this.gesturesEnabled||!finite(s.age_ms)||s.age_ms>700)return;
    if(this.id!==null&&this.epoch!==s.frame.epoch)return;
    let t=s.tracks.find(t=>t.id===this.id&&!t.coasting&&t.conf>=.5);
    if(this.id===null){const raised=s.tracks.filter(t=>!t.coasting&&t.conf>=.5&&t.gesture?.name==='follow_me');if(raised.length===1)t=raised[0];}
    const name=t?.gesture?.name,key=name?t.id+':'+name:'';
    if(key===this.gestureKey)return;this.gestureKey=key;if(!name)return;
    if(name==='follow_me'){if(this.id===null)this.select(t.id,now);if(this.airState==='LANDING')return;this.takeoff();this.setFlightStyle('follow');this.paused=false;this.goodSince=null;}
    else if(name==='stop'&&t.id===this.id)this.paused=true;
    else if(name==='land'&&t.id===this.id)this.land();
    else if(name==='circle'&&t.id===this.id&&this.airState==='AIRBORNE'){this.setFlightStyle('circle');this.paused=false;}
    else if(t.id===this.id&&name==='point_left')this.side=-1;
    else if(t.id===this.id&&name==='point_right')this.side=1;
  }
  select(id,now){this.id=id;this.epoch=this.s?.frame?.epoch;this.instance=this.s?.stats?.instance;this.goodSince=null;this.lastGoodSeq=null;this.requiresSelection=false;this.paused=false;this.mode='ACQUIRE';this.reason='Confirming fresh observations';this.matcher=new ReturnMatcher();this.matcher.faceRequired=this.faceEnabled;this.enrolling=false;this.faceSamples=[];this.faceMessage=this.faceEnabled?'Enroll selected face before leaving view':'Face matching off';this.reacquired=false;const t=this.s?.tracks.find(t=>t.id===id);if(t)this.matcher.remember(t,this.s,now);}
  // The map's coordinate origin changed (position source switched): put the simulated vehicle
  // back at home, at rest, so old coordinates are never carried into the new frame.
  rebase(){this.x=0;this.y=0.8;this.vx=0;this.vy=0;this.heading=0;this.goodSince=null;this.lastGoodSeq=null;this.orbitAngle=null;}
  // The PX4 lab became this page's vehicle (or stopped being it): the illustration is put on the
  // ground at home so it can never be flying unseen, or appear mid-air when the link is dropped.
  ground(){this.airState='GROUNDED';this.altitude=0;this.fault=false;this.paused=false;this.flightStyle='follow';this.rebase();}
  reset(){this.id=null;this.mode='IDLE';this.reason='Choose a person to begin';this.goodSince=null;this.fault=false;this.paused=false;this.enrolling=false;this.faceSamples=[];if(this.matcher)this.matcher.faceProfiles=[];this.faceMessage=this.faceEnabled?'Select a person, then enroll their face':'Face matching off';}
  observation(now,project){
    if(this.id===null)return {reason:'Choose a person to begin'};
    if(this.paused)return {reason:'Paused by operator'};
    if(this.fault)return {reason:'Signal-loss test • simulated interruption'};
    const s=this.s;
    if(!s||now-this.received>800)return {reason:'Connection lost'};
    if(!finite(s.age_ms)||s.age_ms>700||now-this.progress>800)return {reason:'Camera observations stale'};
    if(this.epoch!==s.frame?.epoch){this.requiresSelection=true;return {reason:'Camera or tracker changed • select again'};}
    // Track IDs mean nothing across a detector restart, even if the camera's own run continued.
    if((this.instance??null)!==(s.stats?.instance??null)){this.requiresSelection=true;return {reason:'Detector restarted • select again'};}
    if(this.requiresSelection)return {reason:'Target identity uncertain • select again'};
    if(s.stats?.identity?.target===this.id&&s.stats.identity.confirmed===false)return {reason:identityAdvice(identityStatus(s,this.id))};
    let t=s.tracks?.find(t=>t.id===this.id);
    if(!t||t.coasting){
      // When the camera keeps the identity itself (learned re-ID), this display does not make its
      // own colour-based guess; it adopts the camera's decision when one arrives (app.js).
      const match=s.stats?.identity?null:this.matcher?.find(s,now,this.id);
      if(!match)return {reason:s.stats?.identity?'Target out of view • camera is looking for the same person':(this.matcher?.reason||'Target out of view • waiting for return')};
      this.id=match.id;t=match;this.goodSince=null;this.reacquired=true;
    }
    if(!finite(t.conf)||t.conf<0.45)return {reason:'Target confidence low'};
    this.matcher?.remember(t,s,now);
    const p=project(t,s);
    if(!p||!finite(p.x)||!finite(p.y))return {reason:p?.reason||'Position unavailable • show full body in zone'};
    return {point:p,track:t};
  }
  step(dt,now,project){
    dt=Math.max(0,Math.min(dt,0.05));
    const o=this.observation(now,project);let vx=0,vy=0;
    if(this.airState!=='AIRBORNE'){
      this.goodSince=null;
      if(this.airState==='TAKING OFF'){
        this.altitude=Math.min(1.5,this.altitude+.75*dt);this.mode='TAKEOFF';this.reason='Simulated vertical takeoff';
        if(this.altitude>=1.5)this.airState='AIRBORNE';
      }else if(this.airState==='LANDING'){
        this.mode='LAND';this.reason='Braking, then landing at current position';
        if(Math.hypot(this.vx,this.vy)<.03)this.altitude=Math.max(0,this.altitude-.6*dt);
        if(this.altitude===0){this.airState='GROUNDED';this.mode='GROUND';this.reason='Landed • press Take off to fly again';}
      }else{this.mode='GROUND';this.reason='Grounded • press Take off to lift off here';}
    }else if(o.point){
      if(this.goodSince===null){this.goodSince=now;this.lastGoodSeq=this.seq;}
      if(now-this.goodSince>=500&&this.seq!==this.lastGoodSeq){
        this.mode='FOLLOW';this.reason=this.reacquired?(this.faceEnabled?'Target reacquired • enrolled face matched':'Target reacquired • clothing appearance matched'):'Fresh observations • selected track retained';
        const p=o.point,off=this.settings.gap;
        const d=Math.hypot(p.x,p.y)||1;
        const angle=Math.atan2(p.x,p.y)-this.side*Math.PI/4;
        // Schematic and floor-plane views keep the vehicle between the camera and the person, so
        // the gap cannot exceed the room in front of the camera. With stereo the map is metric with
        // the camera at the origin and the vehicle is a virtual one: it may stand off behind the
        // camera, so the requested gap is honoured (the travel limit below still applies).
        const room=Math.max(0,d-.8),availableOffset=p.source==='stereo'?off:Math.min(off,room);
        this.gapLimited=availableOffset<off-.05?availableOffset:null;
        let gx=p.x-availableOffset*Math.sin(angle),gy=p.y-availableOffset*Math.cos(angle);
        if(this.flightStyle==='circle'){
          // Fit a complete orbit, rather than silently blocking a large requested gap.
          const room=this.settings.radius-Math.hypot(p.x,p.y-.8)-.1;
          const radius=Math.min(this.settings.gap,room);this.orbitRadius=Math.max(0,radius);
          if(this.orbitAngle===null)this.orbitAngle=Math.atan2(this.y-p.y,this.x-p.x);
          gx=p.x+radius*Math.cos(this.orbitAngle);gy=p.y+radius*Math.sin(this.orbitAngle);
          if(radius<.5){
            this.mode='HOLD';this.reason='Circle exceeds travel limit • reduce gap or increase max travel';gx=this.x;gy=this.y;
          }else{
            this.mode='CIRCLE';this.reason=radius<this.settings.gap?'Circling target • radius reduced to fit travel limit':'Circling selected target • gap sets orbit radius';
            if(Math.hypot(gx-this.x,gy-this.y)<.6)this.orbitAngle+=Math.min(.6,this.settings.speed*.65/radius)*dt;
          }
        }
        // Bound simulated travel around home (0, 0.8), independent of target range.
        const travel=Math.hypot(gx,gy-.8);if(travel>this.settings.radius){const k=this.settings.radius/travel;gx*=k;gy=.8+(gy-.8)*k;this.reason='Following within your travel limit';}
        if(this.gapLimited!==null&&this.flightStyle!=='circle'&&this.reason!=='Following within your travel limit')this.reason='Following • gap limited to '+this.gapLimited.toFixed(1)+' m by the space in front of the camera';
        if(p.held)this.reason='Stereo depth held • last value first seen here '+Math.round(p.age_ms)+' ms ago, current direction';
        vx=(gx-this.x)*1.4;vy=(gy-this.y)*1.4;
        const speed=Math.hypot(vx,vy),max=this.settings.speed;if(speed>max){vx*=max/speed;vy*=max/speed;}
      }else{this.mode='ACQUIRE';this.reason='Confirming stable observations';}
    }else{this.goodSince=null;this.reason=o.reason;this.mode=this.id===null?'IDLE':(Math.hypot(this.vx,this.vy)>0.03?'BRAKE':'HOLD');}
    // Loss always targets zero VELOCITY, never the previous destination.
    const ax=vx-this.vx,ay=vy-this.vy,a=Math.hypot(ax,ay),lim=3.5*dt;
    const k=a>lim?lim/a:1;this.vx+=ax*k;this.vy+=ay*k;
    const wasInside=Math.hypot(this.x,this.y-.8)<=this.settings.radius;
    this.x+=this.vx*dt;this.y+=this.vy*dt;
    const travel=Math.hypot(this.x,this.y-.8);
    if(wasInside&&travel>this.settings.radius){const nx=this.x/travel,ny=(this.y-.8)/travel;this.x=nx*this.settings.radius;this.y=.8+ny*this.settings.radius;const outward=this.vx*nx+this.vy*ny;if(outward>0){this.vx-=outward*nx;this.vy-=outward*ny;}}
    const speed=Math.hypot(this.vx,this.vy);
    if(speed>0.03){let d=Math.atan2(this.vx,this.vy)-this.heading;d=Math.atan2(Math.sin(d),Math.cos(d));this.heading+=Math.max(-2*dt,Math.min(2*dt,d));}
    return {...o,speed,mode:this.mode,reason:this.reason};
  }
}
function distance(s,id){
  if(s?.lock?.id!==id||!s.distance?.valid)return null;
  const r=s.distance.est_m??s.distance.raw_m;
  return finite(r)&&r>0&&r<=30?r:null;
}
// OAK stereo depth of the camera-side locked person, as display text. null = stereo is not
// enabled (hide the line). A number is shown ONLY for status "ok" on a fresh state for the
// target this display has selected; everything else says why there is no number.
// z is optical-axis depth, not range or standoff, and is unvalidated against a tape measure.
const STEREO_REASONS={no_target:'no target selected',target_coasting:'target not currently seen',
  unsupported_target_class:'selected target is not a person',no_pair_for_frame:'no depth for this frame',
  pair_wait_expired:'depth not ready in time',skew_exceeded:'depth and image out of step',
  stereo_unavailable:'stereo camera not running',omitted_by_request:'no target selected',
  stale_state:'camera data is stale',no_valid_depth:'no depth on the person',
  insufficient_valid_depth:'too little depth on the person',cluster_too_spread:'depth too scattered',
  no_dominant_cluster:'no clear surface',roi_too_small:'person too small in view',bad_box:'bad target box',
  not_an_oak_pair_frame:'not a stereo frame',estimator_error:'estimator fault',
  frame_not_in_history:'frame too old for stereo',left_right_outside_limit:'stereo cameras out of step',
  rgb_outside_limit:'depth and image out of step',busy:'stereo busy',epoch_changed:'camera restarted',not_ready:'stereo starting'};
function stereo(s,id,fresh){
  const st=s?.stereo;
  if(!st)return null;
  const none=why=>'Stereo depth: unavailable ('+why+')';
  if(!fresh)return none('camera data is stale');
  if(id===null||id===undefined||s?.lock?.id!==id)return none('this display\'s target is not the camera\'s locked target');
  const pct=v=>finite(v)?Math.round(Math.max(0,Math.min(1,v))*100)+'%':'—';
  if(st.status==='ok'){
    if(!(finite(st.z_m)&&st.z_m>0&&st.z_m<=30))return none('value out of range');
    return 'Stereo depth: '+st.z_m.toFixed(2)+' m (unvalidated) · valid pixels '+pct(st.coverage)+' · surface share '+pct(st.support);
  }
  if(st.status==='ambiguous'){
    const zs=(st.candidates||[]).map(c=>c.z_m).filter(z=>finite(z)&&z>0).sort((a,b)=>a-b).map(z=>z.toFixed(2)+' m');
    return 'Stereo depth: ambiguous'+(zs.length?' (unvalidated surfaces at '+zs.join(' and ')+')':'');
  }
  return none(STEREO_REASONS[st.reason]||'no measurement');
}
function solve(A,b){
  const n=b.length;A=A.map((r,i)=>[...r,b[i]]);
  for(let i=0;i<n;i++){let p=i;for(let j=i+1;j<n;j++)if(Math.abs(A[j][i])>Math.abs(A[p][i]))p=j;
    [A[i],A[p]]=[A[p],A[i]];if(Math.abs(A[i][i])<1e-10)throw Error('Points are degenerate');
    const d=A[i][i];for(let k=i;k<=n;k++)A[i][k]/=d;
    for(let j=0;j<n;j++)if(j!==i){const f=A[j][i];for(let k=i;k<=n;k++)A[j][k]-=f*A[i][k];}}
  return A.map(r=>r[n]);
}
function homography(points,width,depth){
  if(points.length!==4||!finite(width)||!finite(depth)||width<=0||depth<=0)throw Error('Use four floor corners and positive dimensions');
  // Convex, non-crossed polygon: near-left, near-right, far-right, far-left.
  const cross=points.map((p,i)=>{const q=points[(i+1)%4],r=points[(i+2)%4];return(q.x-p.x)*(r.y-q.y)-(q.y-p.y)*(r.x-q.x);});
  if(cross.some(v=>Math.abs(v)<0.00001)||!cross.every(v=>Math.sign(v)===Math.sign(cross[0])))throw Error('Click the four corners in order');
  const world=[[-width/2,1],[width/2,1],[width/2,1+depth],[-width/2,1+depth]],A=[],b=[];
  points.forEach((p,i)=>{const [x,y]=world[i];A.push([p.x,p.y,1,0,0,0,-x*p.x,-x*p.y]);b.push(x);A.push([0,0,0,p.x,p.y,1,-y*p.x,-y*p.y]);b.push(y);});
  return {h:solve(A,b),points,width,depth};
}
function inside(p,poly){const signs=poly.map((a,i)=>{const b=poly[(i+1)%poly.length];return(b.x-a.x)*(p.y-a.y)-(b.y-a.y)*(p.x-a.x);});return signs.every(x=>x>=-1e-6)||signs.every(x=>x<=1e-6);}
function project(t,s,cal){
  const w=s.frame.width,h=s.frame.height;
  if(!(w>0&&h>0&&t.w>0&&t.h>0))return null;
  if(cal){
    const p={x:(t.x+t.w/2)/w,y:(t.y+t.h)/h};
    if(t.y+t.h>h*0.98||!inside(p,cal.points))return null;
    const a=cal.h,d=a[6]*p.x+a[7]*p.y+1;if(Math.abs(d)<1e-8)return null;
    return {x:(a[0]*p.x+a[1]*p.y+a[2])/d,y:(a[3]*p.x+a[4]*p.y+a[5])/d,metric:true};
  }
  // Deliberately schematic: image position and box size, no pretend metric depth.
  return {x:((t.x+t.w/2)/w-0.5)*7,y:Math.max(1.8,Math.min(7,5.5-t.h/h*4)),metric:false};
}
// ---- Target position from OAK stereo depth ---------------------------------------------
// Horizontal field of view of the delivered (undistorted) RGB / aligned-depth image, as
// reported by the camera SDK for the stereo mode (bench log 2026-11-12, geometry entry).
const STEREO_HFOV_DEG=66.5275;
// Map point of a tracked person from stereo depth: forward = z, sideways = z * (pixel offset
// from the image centre) / focal length. Origin is the camera. This treats z as depth along
// the optical axis, which is not yet verified, and z itself is unvalidated against a tape.
function stereoPoint(t,s,zOverride){
  const st=s?.stereo,w=s?.frame?.width;
  if(!(w>0&&t&&t.w>0))return null;
  let z=zOverride;
  if(z===undefined){if(!st||st.status!=='ok'||s.lock?.id!==t.id)return null;z=st.z_m;}
  if(!(finite(z)&&z>0&&z<=30))return null;
  const g=st?.geometry;
  if(g&&(!validGeometry(g)||g.width!==w||g.height!==s.frame.height||g.calibration_id!==st.calibration_id||g.epoch!==st.pair?.epoch))return null;
  const fx=g?g.fx:(w/2)/Math.tan(STEREO_HFOV_DEG*Math.PI/360),cx=g?g.cx:w/2;
  return {x:z*((t.x+t.w/2)-cx)/fx,y:z,metric:true,source:'stereo'};
}
// Stereo depth drops out for single frames (ambiguous, too scattered). Hold the last good
// depth briefly, with the CURRENT bearing; after that there is no position and the simulated
// vehicle brakes and holds, exactly as it does when vision is lost. It never falls back to
// the model estimate or the schematic view, which use a different origin.
class StereoRange{
  constructor(holdMs=700){this.holdMs=holdMs;this.last=null;}
  reset(){this.last=null;}
  point(t,s,now){
    const p=stereoPoint(t,s),epoch=s?.frame?.epoch,key=epoch+':'+s?.frame?.seq;
    // The hold clock starts when a measurement is FIRST seen. The same snapshot is handed in
    // again on every animation frame; repeats must not renew it.
    if(p&&!(this.last&&this.last.key===key&&this.last.id===t.id))this.last={z:p.y,id:t.id,epoch,key,t:now};
    const l=this.last,same=l&&l.id===t.id&&l.epoch===epoch,age=same?now-l.t:Infinity;
    if(p&&age>=0&&age<=this.holdMs)return {...p,age_ms:age};
    if(same&&age>=0&&age<=this.holdMs){
      const held=stereoPoint(t,s,l.z);if(held)return {...held,held:true,age_ms:age};
    }
    this.last=same?l:null;
    if(p)return {reason:'Stereo distance unavailable • measurement is stale'};
    const st=s?.stereo;
    const why=!st?'stereo is off':(s.lock?.id!==t.id?'camera is locked on another target':(STEREO_REASONS[st.reason]||(st.status==='ambiguous'?'two surfaces compete':'no measurement')));
    return {reason:'Stereo distance unavailable • '+why};
  }
}
// ---- 3-D point cloud of one stereo frame -------------------------------------------------
// Payload from /stereo_cloud.bin: "GCL1", u16 width, u16 height, width*height u16 depth (mm,
// 0 = none), width*height*3 u8 BGR. Returns null for anything that is not exactly that.
function validGeometry(g){
  return !!g&&g.schema_version===1&&typeof g.epoch==='string'&&g.epoch.length>0&&typeof g.calibration_id==='string'&&g.calibration_id.length>0&&
    Number.isInteger(g.width)&&Number.isInteger(g.height)&&g.width>0&&g.height>0&&g.width<=8192&&g.height<=8192&&
    ['fx','fy','cx','cy'].every(k=>finite(g[k]))&&g.fx>0&&g.fy>0&&g.cx>=0&&g.cx<g.width&&g.cy>=0&&g.cy<g.height&&
    g.distortion==='none'&&['sdk-transformation-metadata','host-opencv-factory-calibration'].includes(g.source)&&g.reference_frame==='unverified'&&g.verified===false;
}
function decodeCloud(buf){
  if(!(buf instanceof ArrayBuffer)||buf.byteLength<8)return null;
  const magic=String.fromCharCode(...new Uint8Array(buf,0,4)),dv=new DataView(buf);let w,h,offset=8,geometry=null,step=null;
  if(magic==='GCL1'){w=dv.getUint16(4,true);h=dv.getUint16(6,true);}
  else if(magic==='GCL2'||magic==='GCL3'){
    const len=dv.getUint32(4,true);if(len>16384||8+len>buf.byteLength)return null;
    let m;try{m=JSON.parse(new TextDecoder().decode(new Uint8Array(buf,8,len)));}catch(e){return null;}
    if(!validGeometry(m.geometry)||![2,4,8].includes(m.step))return null;
    ({width:w,height:h,geometry,step}=m);offset+=len;
    if(w!==Math.ceil(geometry.width/step)||h!==Math.ceil(geometry.height/step))return null;
  }else return null;
  const n=w*h;if(!Number.isInteger(w)||!Number.isInteger(h)||w<=0||h<=0||buf.byteLength!==offset+n*(magic==='GCL3'?6:5))return null;
  const depth=new Uint16Array(n);for(let i=0;i<n;i++)depth[i]=dv.getUint16(offset+i*2,true);
  const offsets=magic==='GCL3'?new Uint8Array(buf,offset+n*5,n):null;
  if(offsets){
    for(let i=0;i<n;i++){
      const dx=offsets[i]%step,dy=Math.floor(offsets[i]/step);
      if(dy>=step||(i%w)*step+dx>=geometry.width||Math.floor(i/w)*step+dy>=geometry.height)return null;
    }
  }
  return {w,h,depth,offsets,bgr:new Uint8Array(buf,offset+n*2,n*3),geometry,step};
}
// Back-project to metres in the camera frame (x right, y down, z forward). Same assumption as
// the follow position: the value is depth along the optical axis. One viewpoint only - this
// is the surface the camera can see, not a model of the room.
function cloudPoints(c,hfovDeg=STEREO_HFOV_DEG,maxM=8){
  // The grid is every step-th pixel starting at pixel 0, so grid index u is image pixel
  // u*step and the principal point scales with it: centre = w/2, not (w-1)/2. Still an
  // approximation of the delivered K (centred principal point, square pixels).
  const g=c.geometry,k=c.step;
  const f=g?g.fx/k:(c.w/2)/Math.tan(hfovDeg*Math.PI/360),fy=g?g.fy/k:f,cx=g?g.cx/k:c.w/2,cy=g?g.cy/k:c.h/2;
  const xyz=new Float32Array(c.w*c.h*3),rgb=new Uint8Array(c.w*c.h*3);let n=0;
  for(let v=0;v<c.h;v++)for(let u=0;u<c.w;u++){
    const i=v*c.w+u,z=c.depth[i]/1000;if(!(z>0&&z<=maxM))continue;
    const dx=c.offsets?(c.offsets[i]%k)/k:0,dy=c.offsets?Math.floor(c.offsets[i]/k)/k:0;
    xyz[n*3]=z*(u+dx-cx)/f;xyz[n*3+1]=z*(v+dy-cy)/fy;xyz[n*3+2]=z;
    rgb[n*3]=c.bgr[i*3+2];rgb[n*3+1]=c.bgr[i*3+1];rgb[n*3+2]=c.bgr[i*3];n++;
  }
  return {n,f,xyz:xyz.subarray(0,n*3),rgb:rgb.subarray(0,n*3)};   // f: focal length of the point grid, px
}
function cloudPivot(pts){
  if(!pts?.n)return null;
  const depths=Float32Array.from({length:pts.n},(_,i)=>pts.xyz[i*3+2]);
  depths.sort();
  return depths[depths.length>>1];
}

// Rotate about a fixed point on the RGB optical axis. Guides and points use the same transform.
function cloudProjection(view,pivot,W,H){
  const cy=Math.cos(view.yaw),sy=Math.sin(view.yaw),cp=Math.cos(view.pitch),sp=Math.sin(view.pitch);
  const dist=pivot*(view.zoom||1),f=W*0.75;
  function transform(x,y,z,out){
    z-=pivot;
    const z1=-x*sy+z*cy;
    out[0]=x*cy+z*sy;out[1]=y*cp-z1*sp;out[2]=y*sp+z1*cp+dist;
    return out;
  }
  function screen(p){return [W/2+f*p[0]/p[2],H/2+f*p[1]/p[2]];}
  function segment(a,b){
    const p=transform(...a,[]),q=transform(...b,[]),near=0.05;
    if(p[2]<near&&q[2]<near)return null;
    if(p[2]<near||q[2]<near){
      const [back,front]=p[2]<near?[p,q]:[q,p];
      const t=(near-back[2])/(front[2]-back[2]);
      back[0]+=t*(front[0]-back[0]);back[1]+=t*(front[1]-back[1]);back[2]=near;
    }
    return [screen(p),screen(q)];
  }
  return {f,transform,segment};
}

function renderCloud(pts,out,W,H,view){
  out.fill(0);for(let i=3;i<out.length;i+=4)out[i]=255;
  if(!pts||!pts.n)return 0;
  const projection=cloudProjection(view,view.pivot??cloudPivot(pts),W,H);
  const f=projection.f,pos=new Float64Array(3),zbuf=new Float32Array(W*H).fill(Infinity);
  let drawn=0;
  for(let i=0;i<pts.n;i++){
    projection.transform(pts.xyz[i*3],pts.xyz[i*3+1],pts.xyz[i*3+2],pos);
    const x1=pos[0],y2=pos[1],z2=pos[2];
    if(z2<=0.05)continue;
    // Splat radius: half the on-screen spacing between neighbouring grid points at this
    // depth, so surfaces look closed at any canvas size without smearing far points.
    const px=Math.round(W/2+f*x1/z2),py=Math.round(H/2+f*y2/z2);
    const r=view.raw?0:pts.f?Math.max(1,Math.min(8,Math.ceil(0.5*f*pts.xyz[i*3+2]/(z2*pts.f)))):(z2<1.5?2:1);
    for(let dy=-r;dy<=r;dy++)for(let dx=-r;dx<=r;dx++){
      const qx=px+dx,qy=py+dy;if(qx<0||qy<0||qx>=W||qy>=H)continue;
      const q=qy*W+qx;if(z2>=zbuf[q])continue;zbuf[q]=z2;
      out[q*4]=pts.rgb[i*3];out[q*4+1]=pts.rgb[i*3+1];out[q*4+2]=pts.rgb[i*3+2];
    }
    drawn++;
  }
  return drawn;
}
// Which coordinate source the simulated vehicle follows. The operator's choice is latched:
// once stereo has been seen and is selected, a snapshot without stereo means "no position"
// (hold), never a silent switch to the floor/schematic projection, which has another origin.
// update() reports when the source actually changes so the caller can rebase the simulation.
class PositionSource{
  constructor(){this.seen=false;this.mode='classic';}
  update(hasStereo,wantStereo){
    if(hasStereo)this.seen=true;
    const next=this.seen&&wantStereo?'stereo':'classic',changed=next!==this.mode;
    this.mode=next;return {mode:next,changed};
  }
}
// Liveness of something fetched repeatedly (3-D cloud, depth picture). A request that never
// settles must not leave old data looking live: begin() allows a new attempt once the previous
// one has timed out, and state() goes 'stale' when nothing has succeeded for staleMs.
class LiveGate{
  constructor(timeoutMs=2500,staleMs=3000){this.timeoutMs=timeoutMs;this.staleMs=staleMs;this.started=null;this.lastOk=null;this.token=0;}
  begin(now){if(this.started!==null&&now-this.started<this.timeoutMs)return null;this.started=now;return ++this.token;}
  timedOut(now){return this.started!==null&&now-this.started>=this.timeoutMs;}
  // A reply counts only if it is for the current request, that request is still pending, and
  // it arrived before the deadline - even when no newer request has been started yet.
  ok(token,now){if(token!==this.token||this.started===null||now-this.started>=this.timeoutMs)return false;this.started=null;this.lastOk=now;return true;}
  cancel(){this.started=null;this.token++;}            // whatever is in flight no longer counts
  fail(token){if(token===this.token)this.started=null;}
  state(now,paused){if(paused)return 'paused';if(this.lastOk===null)return 'waiting';return now-this.lastOk<=this.staleMs?'live':'stale';}
}
// What a repeatedly fetched view may show and say. Kept free of the DOM so the transitions
// (pause, resume, success, failure, staleness) are tested together, not just the gate.
// watch() returns {state, clear, text}; clear=true means "remove what is on screen now".
class LiveDisplay{
  constructor(gate){this.gate=gate;this.paused=false;this.has=false;this.info='';this.error='';this.pendingClear=false;}
  pause(){this.paused=true;}
  // Resuming discards what was shown and anything requested while paused: only a request
  // started after this point may put data back on screen.
  resume(){this.paused=false;this.gate.cancel();this.gate.lastOk=null;this.has=false;this.info='';this.pendingClear=true;}
  success(token,now,info){if(this.paused||!this.gate.ok(token,now))return false;this.has=true;this.info=info;this.error='';return true;}
  failure(token,message){this.gate.fail(token);this.error=message||'';}
  watch(now){
    const state=this.gate.state(now,this.paused);let clear=this.pendingClear;this.pendingClear=false;
    if(state==='stale'&&this.has){this.has=false;this.info='';clear=true;}
    let text=state==='paused'?(this.has?'Paused · showing '+this.info:'Paused · nothing to show'):state==='live'?this.info:state==='stale'?'No live stereo data — last frame cleared':'Connecting…';
    if(this.error&&state!=='live')text+=' · '+this.error;   // the last failure stays visible beside the state
    return {state,clear,text};
  }
}
// ---- Keeping the camera's selected target in step with this display -------------------------
// The camera measures stereo depth only for the one track it has selected, and several
// displays can share one camera. Policy (explicit, astra-0058 finding 3):
//  * A display asserts its target when its target changes: the operator selects here, or this
//    display re-acquires its person under a new track ID. It repeats until the camera confirms.
//  * The agreement that counts is the camera's CURRENT selection in the live state, together
//    with the detector process and frame epoch the confirmation was for. A past confirmation
//    alone is never treated as agreement.
//  * If the camera's selection later moves elsewhere (another display selected someone), this
//    display does NOT take it back by itself - two displays would otherwise fight. It reports
//    'displaced' and the operator selects here again to retake (forget() then re-asserts).
// One request at a time; a reply that never arrives is abandoned after timeoutMs; a reply to
// an abandoned or superseded request confirms nothing.
class TargetSync{
  constructor(timeoutMs=2000,graceMs=600,retryMs=150){this.timeoutMs=timeoutMs;this.graceMs=graceMs;this.retryMs=retryMs;this.acked=null;this.pending=null;this.n=0;this.lastTry=-Infinity;this.timeouts=0;this.failures=0;}
  key(s,id){return JSON.stringify([s?.stats?.instance??null,s?.frame?.epoch??null,id]);}
  forget(){this.acked=null;}
  // A request to send now ({token,id,instance,epoch,track}), or null.
  next(s,id,now){
    if(this.pending&&now-this.pending.t>=this.timeoutMs){this.pending=null;this.timeouts++;}
    if(id===null||id===undefined||!s)return null;
    const instance=s.stats?.instance,epoch=s.frame?.epoch;
    if(typeof instance!=='string'||!instance||typeof epoch!=='string'||!epoch)return null;
    const key=this.key(s,id);
    if(this.acked&&this.acked.key===key)return null;
    if(this.pending||now-this.lastTry<this.retryMs)return null;
    const track=(s.tracks||[]).find(t=>t.id===id&&!t.coasting&&t.label==='person');
    if(!track)return null;
    this.pending={token:++this.n,key,t:now};this.lastTry=now;
    return {token:this.pending.token,id,instance,epoch,track};
  }
  // Outcome of the request with this token. Returns true only when it confirmed something.
  done(token,ok,now){
    if(!this.pending||this.pending.token!==token)return false;
    const p=this.pending;this.pending=null;
    if(!ok){this.failures++;return false;}
    this.acked={key:p.key,t:now};return true;
  }
  // 'idle' | 'unsynced' | 'pending' | 'synced' | 'displaced'
  status(s,id,now){
    if(id===null||id===undefined)return 'idle';
    if(!this.acked||this.acked.key!==this.key(s,id))return this.pending?'pending':'unsynced';
    if(s?.lock?.id===id)return 'synced';
    return now-this.acked.t<this.graceMs?'pending':'displaced';   // the state shown may predate the confirmation
  }
}
// ---- Link from this page to the PX4 + Gazebo lab (simulation only) -------------------------
// Decides what to tell the lab; the page does the talking (lab.js). Only explicit operator
// actions are forwarded. The one thing done unasked is choosing the stereo camera as the lab's
// source while its vehicle is on the ground, because nothing else this page shows applies to it.
// Which lab source this camera may drive, or null. Stereo and the one-lens estimate are
// separate, explicitly chosen sources: neither is ever a fallback for the other.
function labSourceFor(caps){
  if(!caps)return null;
  if(caps.mount==='vehicle')return ['oak','cm3','usb'].includes(caps.camera)?'pi_onboard':null;
  if(caps.camera==='oak'&&caps.distance==='stereo')return 'fixed_stereo';
  if((caps.camera==='cm3'||caps.camera==='usb')&&caps.distance==='mono_estimate')return 'mono_estimate';
  return null;
}
class LabLink{
  constructor(retryMs=2000){this.retryMs=retryMs;this.lastSource=-Infinity;this.lastSelect=-Infinity;}
  // pageId: the person the operator has selected on this display (or null); s: the camera state.
  // These commands are sent unasked, so they are only produced on the ground.
  sync(lab,now,pageId=null,s=null){
    if(!lab)return [];
    const grounded=lab.phase==='GROUND'&&lab.armed===false,kind=labSourceFor(capabilities(s));
    if(kind===null)return [];
    if(lab.source_kind!==kind){
      if(!grounded||now-this.lastSource<this.retryMs)return [];
      this.lastSource=now;return [{command:'source',kind}];
    }
    // A selection the operator already made here before the link came up (or before the lab
    // switched source, which clears its own) is passed on while the vehicle is on the ground.
    // In the air nothing is selected unasked: Take off sends the selection itself.
    const want=this.selection(lab,pageId,s);
    if(want&&grounded&&now-this.lastSelect>=this.retryMs){this.lastSelect=now;return [want];}
    return [];
  }
  // The select command the lab still needs for the display's person, or null.
  selection(lab,pageId,s){
    const kind=labSourceFor(capabilities(s));
    if(!lab||kind===null||kind==='pi_onboard'||lab.source_kind!==kind||pageId===null||pageId===undefined)return null;
    if(lab.target_id===pageId&&!lab.requires_reselection)return null;
    const epoch=s?.frame?.epoch;
    if(typeof epoch!=='string'||!(s.tracks||[]).some(t=>t.id===pageId&&t.label==='person'&&!t.coasting))return null;
    return {command:'select',id:pageId,epoch};
  }
  // Take off and follow when a person is selected here, otherwise just take off and hold.
  takeoff(id){return {command:id===null||id===undefined?'takeoff':'takeoff_follow'};}
  // The page's travel radius is the lab's travel limit; the lab clamps each value to its own range.
  settings(s){return {command:'settings',gap:Number(s.gap),speed:Number(s.speed),limit:Number(s.radius)};}
}
// Which of the bench cameras is feeding the detector. Stereo exists only on the OAK.
function cameraLabel(source,stereo){
  if(typeof source!=='string')return 'CAMERA';
  if(source==='csi')return 'CM3 CAMERA · CSI';
  if(source.startsWith('/dev/'))return 'USB WEBCAM';
  if(source==='oak-rgb')return stereo?'OAK-D PRO · STEREO':'OAK-D PRO · COLOUR ONLY';
  return 'CAMERA';
}
// The camera re-bound the selected person to a new track ID: the display follows that decision.
function adoptIdentity(flightId,s){
  const d=s?.stats?.identity;
  if(flightId===null||flightId===undefined||!d||d.rebound_from!==flightId||!Number.isInteger(d.target))return flightId;
  // Only a confirmed decision made on exactly this frame by this detector process.
  if(d.confirmed!==true||d.frame_seq!==s.frame?.seq||d.frame_epoch!==s.frame?.epoch||d.instance!==s.stats?.instance)return flightId;
  return (s.tracks||[]).some(t=>t.id===d.target&&t.label==='person'&&!t.coasting)?d.target:flightId;
}
// What the camera feeding the detector offers. The detector says so itself; for an older
// detector without that block it is inferred from what the state carries. Never upgraded:
// "stereo" needs the detector's word or an actual stereo block.
// Camera mounted on the vehicle: what the camera computer WOULD tell a vehicle for the frame
// shown. Read-only: this page never forwards it and the detector marks it transmit:false.
// Anything that does not belong to exactly the frame shown, or is not fresh, is shown as HOLD.
function mountOf(s){const c=s?.stats?.capabilities;return {mount:c?.mount==='vehicle'?'vehicle':'fixed',locked:c?.mount_locked===true,recording:c?.onboard_recording===true};}
// Where the operator asked the command to go, and what the sender there says it is doing.
// "Sending" is only ever the sender's own live report, never this page's assumption.
const SEND_NAMES={sim:'PX4 on the PC (simulation, open loop: the real camera does not move with the simulated vehicle)',fc:'the flight controller',fc_hold:'the flight controller'};
const SEND_LISTENER={sim:'sim',fc:'fc',fc_hold:'fc'};
function transmitView(s,fresh=true){
  if(!fresh)return {request:'unknown',sending:false,banner:'SENDING STATE UNKNOWN · NO FRESH CONTACT WITH THE CAMERA COMPUTER',status:'Sending state unknown: no fresh contact with the camera computer.',available:{sim:false,fc:false},real:false};
  const t=s?.transmit,L=t?.listeners||{},request=Object.hasOwn(SEND_LISTENER,t?.request)?t.request:'off',l=request==='off'?null:L[SEND_LISTENER[request]];
  // The follow command is "sending" only for a follow request; a hold request can at most be a zero stream.
  const alive=k=>L[k]?.alive===true,sending=request!=='fc_hold'&&!!l&&l.alive===true&&l.state==='sending';
  const why=l&&l.alive===true&&typeof l.reason==='string'&&l.reason?' ('+l.reason+')':'';
  // Whether the flight-controller bridge has setpoints flowing at all (zero ones included) is its
  // own explicit, current statement. It is shown whatever the request is: after Nowhere the
  // follow command is off but a zero stream may continue. Never inferred from a state name.
  const fc=L.fc,stream=fc?.alive===true?(fc.streaming===true?true:fc.streaming===false?false:null):null;
  const fcWhy=fc?.alive===true&&typeof fc.reason==='string'&&fc.reason?' ('+fc.reason+')':'';
  const streamText=stream===true?'ZERO STREAM CONTINUES to the flight-controller bridge\'s endpoint'+fcWhy:stream===false?'the bridge is sending nothing'+fcWhy:'the bridge\'s stream state is unknown';
  const banner=sending?'SENDING TO '+SEND_NAMES[request].toUpperCase():request==='sim'?'NOT SENDING · '+SEND_NAMES[request]+' is '+(l&&l.alive===true?String(l.state)+why:'not answering')
    :request==='off'&&fc?.alive!==true?'NOT SENT':'FOLLOW COMMAND NOT SENT · '+(fc?.alive===true?streamText:'the flight-controller bridge is not answering');
  const status=request==='off'&&banner==='NOT SENT'?(typeof t?.reason==='string'&&t.reason!=='not requested'&&t.reason!=='switched off by the operator'?'Sending is off: '+t.reason+'.':''):banner+'.';
  return {request,sending,banner,status,stream,available:{sim:alive('sim'),fc:alive('fc')},real:request==='fc'&&sending};
}
const ONBOARD_MAX_AGE_MS=400;   // the page's own bound; a larger limit reported by the detector is not obeyed
function onboardView(s,fresh,ageMs){
  if(mountOf(s).mount!=='vehicle')return null;
  const o=s.onboard,lim=o?.limits||{},num=v=>typeof v==='number'&&Number.isFinite(v);
  const same=!!o&&o.frame_seq===s.frame?.seq&&o.frame_epoch===s.frame?.epoch&&o.instance===s.stats?.instance;
  const limit=Math.min(ONBOARD_MAX_AGE_MS,num(lim.max_age_ms)&&lim.max_age_ms>0?lim.max_age_ms:ONBOARD_MAX_AGE_MS);
  const problem=!fresh||!num(ageMs)||ageMs<0||ageMs>limit?'Observations stale':!o?'No command from the camera computer':o.transmit!==false?'Command not marked as unsent • not shown':!same?'Command is not for the frame shown':(!num(o.yaw_rate)||!num(o.forward_vel))?'Unusable command':null;
  const valid=!problem&&o.valid===true,yaw=valid?o.yaw_rate:0,forward=valid?o.forward_vel:0;
  const frac=(v,limit)=>num(limit)&&limit>0?Math.max(-1,Math.min(1,v/limit)):0;
  const source=o?.range_source==='stereo'?'stereo distance (unvalidated)':o?.range_source==='box_height'?'box size (a proxy, not a distance)':'no distance source';
  const turn=Math.abs(yaw)<.005?'no turn':(yaw>0?'turn right ':'turn left ')+Math.abs(yaw).toFixed(2)+' rad/s';
  const move=Math.abs(forward)<.005?'no forward motion'+(valid&&o.forward_reason&&o.forward_reason!=='ok'?' ('+o.forward_reason+')':''):(forward>0?'forward ':'back ')+Math.abs(forward).toFixed(2)+' m/s';
  const text=String(o?.reason||'hold');
  return {mode:valid?'FOLLOW':'HOLD',style:valid?'FOLLOW':'HOLD',valid,
    reason:problem||(valid?'Would follow person #'+o.target_id:text.charAt(0).toUpperCase()+text.slice(1)),
    detail:(valid?turn+' · '+move:'All speeds zero')+' · '+transmitView(s,fresh).banner,send:transmitView(s,fresh),
    offset:valid&&num(o.offset)?Math.max(-1,Math.min(1,o.offset)):null,
    yaw,forward,yawFrac:frac(yaw,lim.max_yaw_rate),forwardFrac:forward>=0?frac(forward,lim.max_forward_vel):frac(forward,lim.max_back_vel),
    turn,move,range:valid&&num(o.range_m)?o.range_m:null,source,gap:num(lim.gap_m)?lim.gap_m:null,targetId:o?.target_id??null,
    forwardReason:valid?o.forward_reason:null,...mountOf(s)};
}
function capabilities(s){
  const src=s?.frame?.source,c=s?.stats?.capabilities;
  const camera=src==='oak-rgb'?'oak':src==='csi'?'cm3':(typeof src==='string'&&src.startsWith('/dev/'))?'usb':'unknown';
  if(c&&typeof c==='object'&&['stereo','mono_estimate','none'].includes(c.distance_source)){
    const stereo=c.distance_source==='stereo'&&camera==='oak';
    return {camera,distance:stereo?'stereo':c.distance_source==='stereo'?'none':c.distance_source,depthMap:stereo&&c.depth_map===true,cloud:stereo&&c.cloud===true,explicit:true,...(c.mount==='vehicle'?{mount:'vehicle'}:{})};
  }
  const stereo=!!s?.stereo&&camera==='oak';
  return {camera,distance:stereo?'stereo':s?.distance?'mono_estimate':'none',depthMap:stereo,cloud:stereo,explicit:false};
}
// Which stereo-only controls to offer. A camera that states its capabilities is obeyed as it
// is NOW: having seen stereo earlier in this page (the OAK, before the device was switched to
// a one-lens camera) never keeps the depth views on offer. Only a detector that says nothing
// about itself gets the benefit of that memory (its stereo block can be briefly absent).
function depthViews(caps,stereoSeen){
  if(caps.explicit)return {map:caps.depthMap,cloud:caps.cloud,panel:caps.distance==='stereo'};
  const on=caps.distance==='stereo'||stereoSeen===true;
  return {map:on,cloud:on,panel:on};
}
// The large distance readout beside the video: one number, its source and what it is worth.
// null = nothing to show (nobody selected, or this camera publishes no distance).
function rangeReadout(s,id,fresh,caps){
  if(id===null||id===undefined||!caps||caps.distance==='none')return null;
  const stereo=caps.distance==='stereo';
  const out={value:'—',ok:false,source:stereo?'STEREO DEPTH':'MONO ESTIMATE',badge:stereo?'UNVALIDATED':'NOT A MEASUREMENT'};
  if(!fresh)return out;
  if(stereo){const st=s?.stereo;if(st&&s.lock?.id===id&&st.status==='ok'&&finite(st.z_m)&&st.z_m>0&&st.z_m<=30){out.value=st.z_m.toFixed(2)+' m';out.ok=true;}}
  else{const r=distance(s,id);if(r!==null){out.value=r.toFixed(1)+' m';out.ok=true;}}
  return out;
}
// Whether the camera is sure it still has the selected person. null = this detector keeps no
// identity (or it is about somebody else): show nothing rather than a reassuring default.
function identityStatus(s,id){
  const d=s?.stats?.identity;
  if(id===null||id===undefined||!d||d.target!==id)return null;
  // A green badge needs a decision made on exactly the frame shown, by this detector process.
  const current=d.frame_seq===s.frame?.seq&&d.frame_epoch===s.frame?.epoch&&d.instance===s.stats?.instance&&d.instance!==undefined;
  if(!current||!Number.isInteger(d.frame_seq)||!d.frame_epoch||!d.instance)return {level:'warn',text:'WAITING FOR CURRENT IDENTITY'};
  if(d.state==='tracking'&&d.confirmed===true)return {level:'ok',text:Number.isInteger(d.rebound_from)?'SAME PERSON · RE-FOUND (WAS #'+d.rebound_from+')':'SAME PERSON · CONFIRMED'};
  if(d.state==='ambiguous')return {level:'bad',text:'TWO PEOPLE LOOK ALIKE · SELECT AGAIN'};
  if(d.state==='expired'||d.state==='no_reference')return {level:'bad',text:'IDENTITY LOST · SELECT AGAIN'};
  if(d.state==='searching'||d.state==='confirming')return {level:'warn',text:'LOOKING FOR THE SELECTED PERSON'};
  return {level:'warn',text:'CHECKING IDENTITY'};
}
function identityAdvice(status){
  if(status?.level==='bad')return status.text.startsWith('TWO PEOPLE')?'Two people look alike • select again':'Identity lost • select again';
  if(status?.text==='WAITING FOR CURRENT IDENTITY')return 'Waiting for the camera’s current identity decision';
  return 'Camera is checking that this is still the selected person';
}
// The PX4 lab as THE vehicle of this page while the link is on. null = link off (the page's
// own illustration is the vehicle). With the link on there is never a fallback to the
// illustration: no contact means no vehicle shown and no movement commands offered.
// Map frame: origin at the camera, y along its view. The lab's camera is ASSUMED 0.8 m behind
// the vehicle's home, looking along local north.
const LAB_MODES={LANDING:'LAND',PRESTREAM:'TAKEOFF',REQUESTING:'TAKEOFF'};
// Who may command the lab: 'mine' (this tab holds the lease), 'free', 'other' (another display
// holds it: view only here), 'expired' (the lease lapsed, the lab is holding; an explicit
// command takes control again), 'legacy' (a lab without leases).
function labControl(lab,session){
  const c=lab?.control;
  if(!c||typeof c!=='object')return 'legacy';
  if(typeof c.owner==='string')return c.owner===session?'mine':'other';
  return c.expired===true?'expired':'free';
}
// Browser authority and FC telemetry describe different parts of the simulator.
function labFlightStatus(lab,view,request){
  const lease={mine:'Lab controls in this tab',other:'Lab controls in another tab',free:'No active tab',
    expired:'Tab control expired',legacy:'Tab control unknown'};
  const parts=[view?.contact?(lease[view.control]||'Tab control unknown'):'Tab control unknown'];
  if(request)parts.push(request.name+' '+request.status);
  if(!view?.fresh){parts.push('Aircraft state unknown');return parts.join(' · ');}
  const nav={0:'Manual',1:'Altitude',2:'Position',4:'Loiter',5:'Return',14:'Offboard',17:'Takeoff',18:'Land'};
  const mode=Number.isInteger(lab?.nav_state)?nav[lab.nav_state]||'mode '+lab.nav_state:'mode unknown';
  const armed=lab?.armed===true?'armed':lab?.armed===false?'disarmed':'arming unknown';
  const landed=lab?.landed_fresh!==true?'ground contact unverified':lab.landed===true?'on ground':lab?.landed===false?'in air':'ground contact unknown';
  parts.push('PX4 reports '+mode+', '+armed+', '+landed);
  if(Number.isFinite(view.altitude))parts.push(view.altitude.toFixed(1)+' m');
  return parts.join(' · ');
}
function labView(on,lab,session=null,camera=null){
  if(!on)return null;
  if(!lab||typeof lab.phase!=='string')return {contact:false,mode:'NO CONTACT',style:'HOLD',reason:'No contact with the PX4 lab on this computer',detail:'Vehicle state unknown · movement commands disabled',scale:'PX4 LAB · NO CONTACT',kind:null,fresh:false,vehicle:null,target:null,targetId:null,altitude:null,airborne:null,control:null,lastCommand:null,applied:{gap:null,speed:null,limit:null},
    can:{takeoff:false,follow:false,circle:false,land:true,hold:true,auto:false}};
  const grounded=lab.phase==='GROUND'&&lab.armed===false&&lab.landed_fresh!==false&&lab.landed!==false,flying=lab.phase==='FLYING';
  const behavior=typeof lab.behavior==='string'?lab.behavior.toUpperCase():'HOLD';
  // An unknown phase (FAULT, or one this page has never heard of) is shown by its own name, never as take-off.
  const followSuspended=flying&&behavior==='FOLLOW'&&(lab.requires_reselection===true||lab.lock_state!=='LOCKED'||lab.target_id==null);
  const mode=flying?(followSuspended?'FOLLOW SUSPENDED':behavior):grounded?'GROUND':(LAB_MODES[lab.phase]||(lab.phase==='GROUND'?(lab.armed===true?'ARMED ON GROUND':'GROUND UNCONFIRMED'):lab.phase));
  // Missing is not fresh: movement and positions need the lab to say its PX4 telemetry is current.
  const fresh=lab.telemetry_fresh===true;
  const home=Array.isArray(lab.home)&&lab.home.length===2&&lab.home.every(finite)?lab.home:[0,0];
  const toMap=(north,east)=>({x:east-home[1],y:north-home[0]+(lab.source_kind==='pi_onboard'?0:.8)});
  const pos=Array.isArray(lab.position)&&lab.position.length>=2&&finite(lab.position[0])&&finite(lab.position[1])&&fresh?lab.position:null;
  const q=lab.attitude,heading=Array.isArray(q)&&q.length===4&&q.every(finite)?Math.atan2(2*(q[0]*q[3]+q[1]*q[2]),1-2*(q[2]*q[2]+q[3]*q[3])):0;
  const tgt=lab.lock_state==='LOCKED'&&!lab.requires_reselection&&Array.isArray(lab.target)&&lab.target.length===2&&lab.target.every(finite)?toMap(lab.target[0],lab.target[1]):null;
  const kind=lab.source_kind;
  const source=kind==='fixed_stereo'?(lab.accept_preview_stereo_in_simulation===true?'stereo distance (unvalidated)':'stereo distance NOT accepted by the lab')
    :kind==='mono_estimate'?(lab.mono_estimate?.accepted===true?'one-lens estimate · schematic, not measured':'one-lens estimate NOT accepted by the lab'):kind==='pi_onboard'?'on-drone camera · open-loop simulation':'lab source: '+kind;
  const scale=kind==='fixed_stereo'?'PX4 SITL · 1 m GRID · STEREO UNVALIDATED':kind==='mono_estimate'?'PX4 SITL · SCHEMATIC · NOT MEASURED':kind==='pi_onboard'?'PX4 SITL · 1 m GRID · HOME ORIGIN':'PX4 SITL · LAB\'S OWN SOURCE';
  const altitude=fresh&&finite(lab.altitude)?Math.max(0,lab.altitude):null;
  const hasTarget=lab.target_id!==null&&lab.target_id!==undefined&&!lab.requires_reselection;
  const control=labControl(lab,session),mayMove=control!=='other'&&fresh;
  let reason=lab.error||lab.reason||'';
  if(!lab.error&&kind==='pi_onboard'){
    reason=reason.replace(/; choose Nowhere then PX4 on the PC again$/, '');
    if(reason==='Sending to PC is off')reason=grounded?'Take off when ready. To follow, select a person and press Follow.':'To follow, select a person and press Follow.';
  }
  // Older labs publish only a generic identity hold. Qualify it with the camera's
  // latest decision only for the same source, detector and target.
  if(!lab.error&&reason==='Camera is checking that this is still the selected person • hold'){
    const sameSource=camera?.frame?.epoch&&camera.frame.epoch===lab.frame?.epoch&&
      camera.stats?.instance&&camera.stats.instance===lab.source_instance;
    const status=sameSource?identityStatus(camera,lab.target_id):null;
    reason=lab.requires_reselection?'PX4 holding • select again':
      status?.level==='ok'?'PX4 holding • waiting for lab confirmation':
      status?'PX4 holding • '+identityAdvice(status):'PX4 holding • awaiting current identity data';
  }
  if(!fresh)reason='PX4 telemetry is not fresh · movement commands disabled'+(reason?' · '+reason:'');
  if(control==='other')reason='View only · another display controls the PX4 lab'+(reason?' · '+reason:'');
  else if(control==='expired'){
    const leaseReason='Tab control lapsed · an explicit flight request is required';
    reason=reason&&reason!=='Browser control lease expired; explicitly resume'?leaseReason+' · '+reason:leaseReason;
  }
  const lc=lab.control?.last_command;
  return {contact:true,mode:fresh?mode:'UNKNOWN',style:fresh&&['GROUND','TAKEOFF','LAND','FOLLOW','CIRCLE','HOLD'].includes(mode)?mode:'HOLD',
    reason,detail:'PX4 SITL · '+(altitude===null?'—':altitude.toFixed(1)+' m')+' · '+source,scale,kind,
    vehicle:pos?{...toMap(pos[0],pos[1]),heading}:null,target:fresh?tgt:null,targetId:lab.target_id??null,altitude,airborne:fresh?!grounded:null,behavior,control,fresh,
    lastCommand:lc&&typeof lc==='object'?lc:null,
    // What the lab says it applies; the page's sliders are only requests.
    applied:{gap:finite(lab.gap)?lab.gap:null,speed:finite(lab.speed)?lab.speed:null,limit:finite(lab.limit)?lab.limit:null},
    // auto: whether the page may send its unasked housekeeping commands (source, selection).
    can:{takeoff:mayMove&&grounded,follow:mayMove&&flying&&(hasTarget||kind==='pi_onboard'),circle:mayMove&&flying&&hasTarget&&kind!=='pi_onboard',land:!fresh||!grounded,hold:!fresh||!grounded,auto:control==='mine'||control==='free'||control==='legacy'}};
}
// Switching the link off must not leave a flying simulated vehicle following with nobody
// watching it: ask for hold, and unlink only when a state RECEIVED AFTER the request reports
// hold (a 202 from the lab only means "queued"). No contact: unlink, but say the state is unknown.
class Unlinker{
  constructor(timeoutMs=4000){this.timeoutMs=timeoutMs;this.since=null;this.note='';this.id=null;this.acked=false;this.failed=null;}
  get pending(){return this.since!==null;}
  // Returns {unlink:bool, send:command|null}. Call on the click, then on every lab state.
  // known.contacted: during this link the lab has answered or was sent a command (kept across a
  // reload). known.takeoffPending: a take-off left this page and the lab is not yet known to
  // have dealt with it, so a state still saying GROUND is not "landed".
  request(view,now,known={}){
    this.note='';this.id=null;this.acked=false;this.since=null;this.failed=null;
    if(!view)return {unlink:true,send:null};
    if(!view.contact||!view.fresh){
      // The only link that may be dropped without the lab's word is one on which the lab never
      // answered and nothing was ever sent: there is nothing this page could have set in motion.
      if(!view.contact&&known.contacted!==true){this.note='Unlinked · the PX4 lab never answered';return {unlink:true,send:null};}
      // Otherwise the vehicle's state is unknown. Unlinking must still work (owner, 2026-11-16):
      // a page that cannot reach the lab cannot do anything for its vehicle by staying linked,
      // and the lab holds by itself when this page's control heartbeat stops. Hold is still
      // sent when there is contact, and the page says plainly that nothing was confirmed.
      this.note='Unlinked WITHOUT the PX4 lab\'s confirmation ('+(view.contact?'PX4 telemetry is not fresh':'no contact with the lab')+') · its vehicle\'s state is unknown · vehicle state is unknown; this tab is disconnected';
      return {unlink:true,send:view.contact?{command:'hold'}:null};
    }
    if(view.airborne===false&&known.takeoffPending!==true)return {unlink:true,send:null};
    this.since=now;return {unlink:false,send:{command:'hold'}};
  }
  // The lab's own id for the hold it queued (labs with command acknowledgement). ok=false: not delivered.
  sent(ok,id){if(this.since===null)return;if(!ok)this.failed='Hold could not be delivered to the PX4 lab';else if(typeof id==='string')this.id=id;}
  // stateAt: when the lab state behind `view` was received.
  update(view,stateAt,now,takeoffPending=false){
    if(this.since===null)return {unlink:false,send:null};
    // Every way of not getting the confirmation ends the wait by unlinking, and says so: the
    // link can never be left stuck. Only the confirmed path unlinks without a warning.
    const give=why=>{this.since=null;this.failed=null;this.note='Unlinked WITHOUT confirmation: '+why+' · vehicle state is unknown; this tab is disconnected';return {unlink:true,send:null};};
    if(this.failed)return give(this.failed);
    if(view&&view.contact&&view.fresh&&stateAt>this.since){
      const lc=view.lastCommand;
      if(this.id!==null&&lc&&lc.id===this.id){
        if(lc.applied===true)this.acked=true;
        else return give('the PX4 lab refused hold'+(lc.error?' ('+lc.error+')':''));
      }
      // With an id, the lab must have applied THAT hold; a vehicle that merely happens to be
      // holding is not the confirmation. Without ids (older lab) the fresh state is all there is.
      const confirmed=this.id===null||view.control==='legacy'||this.acked;
      if(view.airborne===false?takeoffPending!==true:(confirmed&&view.behavior==='HOLD')){this.since=null;return {unlink:true,send:null};}
    }
    if(now-this.since>this.timeoutMs)return give('hold was not confirmed by the PX4 lab in time');
    return {unlink:false,send:null};
  }
}
// Map bounds that follow the action without jumping: they grow at once when something nears
// the edge and shrink only after it has stayed smaller for a while, in fixed steps.
class MapFit{
  constructor(shrinkMs=3000,steps=[2,3,4,6,8,12,16]){this.shrinkMs=shrinkMs;this.steps=steps;this.cur=null;this.smaller={};}
  snap(v){return this.steps.find(s=>s>=v)??this.steps[this.steps.length-1];}
  // points: [{x,y}] in map metres (camera at the origin, which is always kept in view).
  update(points,now){
    const ps=points.filter(p=>p&&finite(p.x)&&finite(p.y)),m=1;
    const need={halfX:this.snap(Math.max(1,...ps.map(p=>Math.abs(p.x)))+m),maxY:this.snap(Math.max(1,...ps.map(p=>p.y))+m),
      minY:Math.min(0,...ps.map(p=>p.y))<0?-this.snap(-Math.min(0,...ps.map(p=>p.y))+m):0};
    if(!this.cur){this.cur=need;return {...this.cur};}
    for(const k of ['halfX','maxY','minY']){
      const bigger=k==='minY'?need[k]<this.cur[k]:need[k]>this.cur[k];
      if(bigger){this.cur[k]=need[k];delete this.smaller[k];}
      else if(need[k]===this.cur[k])delete this.smaller[k];
      else{if(!(k in this.smaller))this.smaller[k]=now;if(now-this.smaller[k]>=this.shrinkMs){this.cur[k]=need[k];delete this.smaller[k];}}
    }
    return {...this.cur};
  }
  reset(){this.cur=null;this.smaller={};}
}
const api={labFlightStatus,mountOf,onboardView,transmitView,recognise,validPeople,cleanName,depthViews,capabilities,rangeReadout,identityStatus,labView,labControl,labSourceFor,Unlinker,MapFit,cameraLabel,adoptIdentity,TargetSync,LabLink,Flight,ReturnMatcher,similarity,distance,stereo,stereoPoint,StereoRange,PositionSource,LiveGate,LiveDisplay,decodeCloud,cloudPoints,cloudPivot,cloudProjection,renderCloud,homography,project,inside};
if(typeof module!=='undefined')module.exports=api;root.Garuda=api;
})(typeof globalThis!=='undefined'?globalThis:this);
