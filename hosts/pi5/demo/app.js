'use strict';
{const u=new URL(location.href);u.port='8099';u.pathname='/';u.search='';u.hash='';const link=document.getElementById('calibrationLink');if(link)link.href=u.href;}
const $=id=>document.getElementById(id),F=new Garuda.Flight();
let S=null,cal=null,savedCal=null,calPoints=null,calSignature=null,previousMode='',lastPaint=0,lastTrail=0;
let trail=[],targetTrail=[],events=[],reduced=matchMedia('(prefers-reduced-motion:reduce)').matches,hadHold=false;
let lastPeople='',gx=0.5,gy=0.5,gh=1,videoError=false;
// V: the PX4 lab as this page's ONE vehicle while the link is on (set by lab.js), else null.
let O=null,V=null,caps=Garuda.capabilities(null);const mapFit=new Garuda.MapFit();
function vehicleChanged(){F.ground();trail=[];targetTrail=[];mapFit.reset();hadHold=false;}
const signature=s=>[s.frame.source,s.frame.width,s.frame.height].join(':');
try{savedCal=JSON.parse(localStorage.getItem('garuda-floor-v1'));if(savedCal)$('calConfirm').hidden=false;}catch(e){}
$('motionToggle').checked=reduced;
// Follow position from stereo depth whenever the camera publishes it and the operator leaves it on.
const stereoRange=new Garuda.StereoRange(),posSource=new Garuda.PositionSource();
// Latched: once stereo has been seen and is selected, a missing stereo block means "hold".
function stereoFollowing(){return posSource.mode==='stereo';}
function updatePositionSource(){const r=posSource.update(!!(S&&S.stereo),$('stereoFollow').checked);
 if(r.changed){stereoRange.reset();F.rebase();trail=[];targetTrail=[];}}
$('motionToggle').onchange=e=>reduced=e.target.checked;
const es=new EventSource('/api/events');
es.onmessage=e=>{try{const next=JSON.parse(e.data);if(!next.frame||!Array.isArray(next.tracks))return;
  S=next;{const kept=Garuda.adoptIdentity(F.id,S);if(kept!==F.id){F.id=kept;F.goodSince=null;F.reacquired=true;}}
  F.receive(S,performance.now());
  if(cal&&(calSignature!==signature(S)||cal.cameraEpoch!==S.frame.epoch)){const id=F.id;clearCalibration();F.id=id;F.requiresSelection=true;$('calStatus').textContent='Camera or tracker restarted. Mark the floor corners again.';F.paused=true;}
}catch(err){console.warn('Invalid perception update',err);}};
es.onerror=()=>{$('connection').textContent='RECONNECTING';};
$('feed').onerror=()=>{videoError=true;setTimeout(()=>{$('feed').src='/stream?clean=1&retry='+Date.now();},2500);};
$('feed').onload=()=>videoError=false;
function select(id){
  if(!S)return;const t=S.tracks.find(t=>t.id===id&&!t.coasting&&t.label==='person');if(!t)return;
  F.select(id,performance.now());trail=[];targetTrail=[];hadHold=false;
  // An explicit selection here always (re)asserts this display's target on the camera.
  targetSync.forget();
}
// Tell the camera which track this display follows, by exact track ID (policy and bookkeeping:
// Garuda.TargetSync). A click by position could land on an overlapping box, so it is used only
// for a detector that has no exact-ID route. That older route answers HTTP 200 even when it
// picked nothing ({"ok":false}), so only a parsed body with ok===true counts as success.
const targetSync=new Garuda.TargetSync();
function sendTargetSync(req){const done=ok=>targetSync.done(req.token,ok,performance.now()),limit=()=>AbortSignal.timeout(1500);
  fetch('/api/select_track?id='+req.id+'&epoch='+encodeURIComponent(req.epoch)+'&instance='+encodeURIComponent(req.instance),{method:'POST',signal:limit()})
   .then(r=>{if(r.status!==404)return done(r.ok);
     return fetch('/api/mono_click?x='+(req.track.x+req.track.w/2)+'&y='+(req.track.y+req.track.h/2),{method:'POST',signal:limit()}).then(r2=>r2.ok?r2.json().then(body=>done(!!body&&body.ok===true)):done(false));})
   .catch(()=>done(false));}
function clearCalibration(){cal=null;calSignature=null;savedCal=null;calPoints=null;localStorage.removeItem('garuda-floor-v1');$('calConfirm').hidden=true;$('calStatus').textContent='Schematic view. No metric position claim.';F.reset();trail=[];targetTrail=[];}
$('reset').onclick=()=>{F.reset();hadHold=false;trail=[];targetTrail=[];};
$('pause').onclick=()=>{F.paused=!F.paused;F.goodSince=null;};
$('fault').onclick=()=>{if(F.id===null){$('story').textContent='Select a person first, then test signal loss.';return;}F.fault=true;F.goodSince=null;};
$('resume').onclick=()=>{F.fault=false;F.goodSince=null;};
$('fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen().catch(()=>{});};
$('calClear').onclick=clearCalibration;
$('calStart').onclick=()=>{if(!S)return;F.reset();calPoints=[];$('calStatus').textContent='Click NEAR-LEFT floor corner in the live image.';};
$('calConfirm').onclick=()=>{if(!S||!savedCal)return;if(savedCal.signature!==signature(S)){clearCalibration();$('calStatus').textContent='Saved camera differs. Mark four corners again.';return;}
  try{cal=Garuda.homography(savedCal.points,savedCal.width,savedCal.depth);cal.cameraEpoch=S.frame.epoch;calSignature=signature(S);$('calStatus').textContent='Saved floor calibration confirmed for this camera position.';$('calConfirm').hidden=true;F.reset();}catch(e){clearCalibration();}};
function fitImage(){const r=$('overlay').getBoundingClientRect(),fw=S?.frame.width||1280,fh=S?.frame.height||720,k=Math.min(r.width/fw,r.height/fh);return{r,k,ox:(r.width-fw*k)/2,oy:(r.height-fh*k)/2,fw,fh};}
$('overlay').onclick=e=>{if(!S)return;const a=fitImage(),x=(e.clientX-a.r.left-a.ox)/a.k,y=(e.clientY-a.r.top-a.oy)/a.k;if(x<0||y<0||x>a.fw||y>a.fh)return;
  if(calPoints){calPoints.push({x:x/a.fw,y:y/a.fh});const names=['NEAR-LEFT','NEAR-RIGHT','FAR-RIGHT','FAR-LEFT'];
    if(calPoints.length<4){$('calStatus').textContent='Click '+names[calPoints.length]+' floor corner.';return;}
    try{const width=Number($('calWidth').value),depth=Number($('calDepth').value);if(width<0.5||width>12||depth<0.5||depth>12)throw Error('Dimensions must be 0.5–12 m');
      cal=Garuda.homography(calPoints,width,depth);cal.cameraEpoch=S.frame.epoch;calSignature=signature(S);savedCal={points:calPoints,width,depth,signature:calSignature};localStorage.setItem('garuda-floor-v1',JSON.stringify(savedCal));$('calStatus').textContent='Floor mapping active. Verify with a person at each marked corner. Only visible feet inside the zone are accepted.';
    }catch(err){cal=null;$('calStatus').textContent=err.message+' — please retry.';}calPoints=null;return;}
  const candidates=S.tracks.filter(t=>t.label==='person'&&!t.coasting&&x>=t.x&&x<=t.x+t.w&&y>=t.y&&y<=t.y+t.h).sort((a,b)=>a.w*a.h-b.w*b.h);
  if(candidates.length)select(candidates[0].id);
};
function canvas(id){const c=$(id),r=c.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2),w=Math.round(r.width*d),h=Math.round(r.height*d);if(c.width!==w||c.height!==h){c.width=w;c.height=h;}const g=c.getContext('2d');g.setTransform(d,0,0,d,0,0);g.clearRect(0,0,r.width,r.height);return {g,w:r.width,h:r.height};}
function updateUI(o,now){
  const age=S&&Number.isFinite(S.age_ms)?Math.max(0,Math.round(S.age_ms+now-F.received)):null;
  const fresh=age!==null&&age<700&&now-F.progress<800&&now-F.received<800;
  $('connection').textContent=fresh?'PERCEPTION LIVE':'WAITING FOR FRESH VIDEO';$('liveDot').className=fresh?'live':'';
  caps=Garuda.capabilities(S);O=V?null:Garuda.onboardView(S,fresh,age);const M=O?O.mode:V?V.mode:F.mode;
  $('source').textContent=Garuda.cameraLabel(S?.frame.source,caps.distance==='stereo');
  $('videoStatus').hidden=fresh&&!videoError;$('videoStatus').textContent=videoError?'Video connection interrupted':(age===null?'Connecting to the camera…':'Observations stale — simulation holds');
  $('welcome').hidden=F.id!==null||calPoints!==null;
  $('mode').textContent=M;$('reason').textContent=O?O.reason:V?V.reason:F.reason;$('decision').dataset.mode=O?O.style:V?V.style:F.mode;
  $('vehiclePill').textContent=O?'CAMERA ON VEHICLE · '+(O.send.sending?O.send.request==='fc'?'SENDING TO FLIGHT CONTROLLER':'SENDING TO PX4 SIMULATOR':'NOT SENT'):V?'PX4 SITL · SIMULATED':'SIMULATED';$('map').parentElement.querySelector('.maplegend').hidden=!!O;$('vehicleLegend').textContent=V?'Simulated PX4 vehicle (lab)':'Simulated vehicle';
  $('motion').textContent=O?O.detail:V?V.detail:stereoFollowing()?'Simulated speed '+o.speed.toFixed(1)+' m/s · '+F.settings.gap.toFixed(1)+' m gap goal · stereo distance (unvalidated)':cal?'Simulated speed '+o.speed.toFixed(1)+' m/s · '+F.settings.gap.toFixed(1)+' m gap goal':'Illustrative motion · '+F.settings.gap.toFixed(1)+' gap setting · calibrate for metres';
  $('mapScale').textContent=O?'COMMAND VIEW · NOT A MAP':V?V.scale:stereoFollowing()?'STEREO DEPTH · 1 m GRID · UNVALIDATED':cal?'FLOOR PLANE ESTIMATE · 1 m GRID · ORIGIN: NEAR-EDGE CENTRE':'SCHEMATIC · NOT TO SCALE · NO METRIC POSITION';
  // Linked: Pause is the lab's Hold, and the signal-loss test (which only ever interrupted this
  // page's illustration) is not offered at all.
  $('pause').textContent=V?'Hold':F.paused?'Resume':'Pause';$('pause').disabled=V?!V.can.hold:false;$('pause').hidden=!!O;
  $('fault').hidden=!!V||!!O;$('resume').hidden=!!V||!!O||!F.fault;$('fault').disabled=F.id===null||F.fault;
  {const m=Garuda.mountOf(S),sel=$('mountSelect');if(document.activeElement!==sel)sel.value=m.mount;sel.disabled=m.locked||!!V;$('mountStatus').textContent=m.locked?'Set by the camera computer\'s configuration.':V?'Unlink the PX4 lab before changing the mount.':'';
   $('carryRecord').hidden=!O;$('carryRecord').disabled=m.recording;$('carryRecord').textContent=m.recording?'Recording… (numbers only)':'Record 2 min (numbers only)';}
  $('presentationNote').textContent=O?'Camera on the vehicle: the panel shows the turn and speed the camera computer would ask for. It is sent only where you choose under the panel, and only while a sender there is running; the fixed-camera PX4 lab link is not available in this mode.':V?'Linked to the PX4 lab: the buttons command the simulated PX4 vehicle. Hold replaces Pause. The signal-loss test is not available while linked.':'Signal-loss testing only interrupts this browser\'s simulation. Camera and inference continue. Each display has its own selected target and simulation.';
  $('targetLabel').textContent=F.id===null?'Choose your target':'Selected '+(F.nameOf(F.id)||'person')+' #'+F.id+(targetSync.status(S,F.id,now)==='displaced'?' · camera is on another target — click your box to retake':'');
  const people=(S?.tracks||[]).filter(t=>t.label==='person'&&!t.coasting);
  const key=people.map(t=>t.id+(F.nameOf(t.id)||'')).join(',')+':'+F.id;
  if(key!==lastPeople){lastPeople=key;$('targets').replaceChildren();people.forEach(t=>{const b=document.createElement('button');b.textContent='#'+t.id+(F.nameOf(t.id)?' '+F.nameOf(t.id):'');b.className=t.id===F.id?'selected':'';b.onclick=()=>select(t.id);$('targets').append(b);});}
  $('fps').textContent=fresh&&Number.isFinite(S?.stats.fps)?S.stats.fps.toFixed(1):'—';$('people').textContent=fresh?people.length:'—';$('age').textContent=age!==null?(age>9999?'>9.9 s':age+' ms'):'—';
  const temp=S?.stats.temp_c;$('temp').textContent=Number.isFinite(temp)?temp.toFixed(0)+'°C':'—';$('temp').style.color=temp>=80?'#ffbf69':'';
  $('thermal').textContent=temp>=80?'High temperature — fit active cooling':'';
  const range=Garuda.distance(S,F.id);$('range').textContent='Depth estimate: '+(fresh&&range!==null?range.toFixed(2)+' m (model estimate; verify physically)':'unavailable / invalid');
  const stereoText=Garuda.stereo(S,F.id,fresh);$('stereo').hidden=stereoText===null;if(stereoText!==null)$('stereo').textContent=stereoText;
  {const dv=Garuda.depthViews(caps,posSource.seen);$('depthPanel').hidden=!dv.panel;$('depthButton').hidden=!dv.map;$('cloudButton').hidden=!dv.cloud;}
  {const r=Garuda.rangeReadout(S,F.id,fresh,caps),box=$('rangeBig');box.hidden=r===null;
   if(r){$('rangeValue').textContent=r.value;$('rangeSource').textContent=r.source;$('rangeBadge').textContent=r.badge;box.dataset.ok=String(r.ok);box.title=stereoText||'';}
   const idn=fresh?Garuda.identityStatus(S,F.id):null,chip=$('identityChip');chip.hidden=idn===null;if(idn){chip.textContent=idn.text;chip.dataset.level=idn.level;}}
  if(M==='HOLD'||M==='BRAKE')hadHold=true;
  const j=F.id===null?1:(M==='BRAKE'||M==='HOLD'?3:(hadHold?4:2));for(let i=1;i<=4;i++)$('j'+i).className=i===j?'active':'';
  const stories={IDLE:['Movement earns trust.','Choose yourself in the live view. Walk sideways, hide briefly, then return. The simulated vehicle responds to fresh tracking observations.'],ACQUIRE:['A moment to be sure.','Waiting for a stable sequence of confident observations before movement resumes.'],FOLLOW:[hadHold?'Back in view. Back in motion.':'You lead. Intelligence follows.','The selected track drives this simulated vehicle. Camera perception runs on the Pi with Hailo-10H acceleration.'],BRAKE:['Lost vision. Deliberate stop.','Movement is braking to zero. The simulation does not continue toward the old target destination.'],HOLD:['Trust includes knowing when to stop.',F.reason+'. '+(F.paused?'Resume when ready.':F.faceEnabled?'Return requires a clear enrolled-face match; uncertain matches keep the vehicle holding.':S?.stats?.identity?'The camera is looking for the same person by learned appearance; an uncertain match keeps the vehicle holding.':'Waiting for a clear clothing match; similar-looking candidates keep the vehicle holding.') ]};
  stories.GROUND=['Ready on the ground.','Select a person, then press Take off. All flight commands control only this simulation.'];
  stories.TAKEOFF=['Taking off.','The simulated vehicle climbs vertically before following the selected target.'];
  stories.LAND=['Coming down.','The simulated vehicle brakes, then descends at its current position. Take off is required to fly again.'];
  stories.CIRCLE=['A new perspective.','The simulated vehicle circles the selected target. Follow gap sets the circle radius; loss of vision brakes and holds.'];
  if(V){const what=V.kind==='fixed_stereo'?'The camera\'s stereo distance (unvalidated) and an assumed camera position':V.kind==='mono_estimate'?'A schematic position from the size of your box in the picture (one lens, not a measured distance)':'The lab\'s own source';
    Object.assign(stories,{GROUND:['Ready on the ground.','Select a person, then press Take off. The buttons command the simulated PX4 vehicle in the lab on this computer.'],
      TAKEOFF:['Taking off.','The simulated PX4 vehicle climbs before it follows the selected person.'],LAND:['Coming down.','The simulated PX4 vehicle is landing where it is.'],
      FOLLOW:['You lead. Intelligence follows.',what+(V.kind==='mono_estimate'?' steers':' steer')+' the simulated PX4 vehicle.'],CIRCLE:['A new perspective.','The simulated PX4 vehicle circles the selected person.'],
      HOLD:['Trust includes knowing when to stop.',(V.reason||'Holding position')+'.'],
      'NO CONTACT':['No contact with the PX4 lab.','This page is linked to the PX4 lab but cannot reach it. The vehicle\'s state is unknown, and nothing is animated here in its place.']});}
  if(O)Object.assign(stories,{FOLLOW:['You lead. This is what it would do.','The camera computer turns the selected person\'s position in the picture into a turn rate, and '+O.source+' into a forward speed. '+(O.send.sending?'It is being sent to '+(O.send.request==='fc'?'the flight controller':'the PX4 simulator on the PC')+'.':'It is shown here and not sent.')],HOLD:['Trust includes knowing when to stop.',O.reason+'. Every speed is zero until the selected person is seen clearly again.']});
  const story=stories[M]||stories.HOLD;$('headline').textContent=story[0];$('story').textContent=story[1];
  renderSend();
  if(previousMode!==M){events.unshift(new Date().toLocaleTimeString()+'  '+M+' — '+(O?O.reason:V?V.reason:F.reason));events=events.slice(0,5);$('eventlog').replaceChildren(...events.map(e=>{const p=document.createElement('div');p.textContent=e;return p;}));previousMode=M;}
  if(typeof updateControls==='function')updateControls(o,now);
  if(window.renderFlightSetup)window.renderFlightSetup();
}
function drawOverlay(){const {g,w,h}=canvas('overlay');if(!S)return;const a=fitImage();
  const poly=calPoints||cal?.points;if(poly?.length){g.strokeStyle='#6de3e1';g.fillStyle='#6de3e115';g.lineWidth=2;g.beginPath();poly.forEach((p,i)=>{const x=a.ox+p.x*a.fw*a.k,y=a.oy+p.y*a.fh*a.k;i?g.lineTo(x,y):g.moveTo(x,y);});if(cal)g.closePath();g.stroke();g.fill();poly.forEach((p,i)=>{g.fillStyle='#6de3e1';g.fillText(String(i+1),a.ox+p.x*a.fw*a.k+5,a.oy+p.y*a.fh*a.k-5);});}
  S.tracks.filter(t=>t.label==='person').forEach(t=>{const x=a.ox+t.x*a.k,y=a.oy+t.y*a.k,ww=t.w*a.k,hh=t.h*a.k,selected=t.id===F.id;
    g.strokeStyle=selected?(t.coasting?'#ffbf69':'#6de3e1'):'#ffffff70';g.lineWidth=selected?3:1;g.setLineDash(t.coasting?[6,5]:[]);g.strokeRect(x,y,ww,hh);g.setLineDash([]);g.font='600 12px sans-serif';const text=(selected?'SELECTED ':'PERSON ')+'#'+t.id+(F.nameOf(t.id)?' · '+F.nameOf(t.id).toUpperCase():'');g.fillStyle=selected?'#6de3e1':'#edf3fa';g.fillText(text,x+5,Math.max(15,y-7));});
  if(F.gesturesEnabled)for(const t of S.tracks){if(!t.kpts||t.coasting)continue;g.strokeStyle=t.id===F.id?'#6de3e1':'#ffffff66';g.lineWidth=2;for(const [i,j]of[[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12]]){const p=t.kpts[i],q=t.kpts[j];if(p[2]<.35||q[2]<.35)continue;g.beginPath();g.moveTo(a.ox+p[0]*a.k,a.oy+p[1]*a.k);g.lineTo(a.ox+q[0]*a.k,a.oy+q[1]*a.k);g.stroke();}}
  if(F.faceEnabled)for(const t of S.tracks){if(!t.face||t.face.age_ms>1800)continue;const [x,y,w,h]=t.face.bbox;g.strokeStyle='#ffbf69';g.lineWidth=1;g.strokeRect(a.ox+x*a.k,a.oy+y*a.k,w*a.k,h*a.k);}
}
function drawGimbal(dt){const {g,w,h}=canvas('gimbal'),feed=$('feed');if(!S||!feed.naturalWidth)return;const t=['FOLLOW','CIRCLE'].includes(F.mode)?S.tracks.find(t=>t.id===F.id&&!t.coasting):null,fw=S.frame.width,fh=S.frame.height;
  const k=reduced?1:1-Math.exp(-5*dt);if(t){gx+=((t.x+t.w/2)/fw-gx)*k;gy+=((t.y+t.h/2)/fh-gy)*k;gh+=(Math.min(1,Math.max(.35,t.h/fh*1.8))-gh)*k;}
  let sh=gh*fh,sw=sh*w/h;if(sw>fw){sw=fw;sh=sw*h/w;}const x=Math.max(0,Math.min(fw-sw,gx*fw-sw/2)),y=Math.max(0,Math.min(fh-sh,gy*fh-sh/2));
  try{g.drawImage(feed,x*feed.naturalWidth/fw,y*feed.naturalHeight/fh,sw*feed.naturalWidth/fw,sh*feed.naturalHeight/fh,0,0,w,h);}catch(e){}
  g.strokeStyle='#6de3e1';g.beginPath();g.moveTo(w/2-10,h/2);g.lineTo(w/2+10,h/2);g.moveTo(w/2,h/2-10);g.lineTo(w/2,h/2+10);g.stroke();
  if(F.mode==='HOLD'||F.mode==='BRAKE'){g.fillStyle='#06101a99';g.fillRect(0,0,w,h);g.fillStyle='#ffbf69';g.font='11px sans-serif';g.fillText('GIMBAL HOLD',10,h-12);}
}
$('mountSelect').onchange=async()=>{const value=$('mountSelect').value;$('mountStatus').textContent='';
  try{const r=await fetch('/api/mount?value='+value,{method:'POST'}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.reason||'Mount change refused');}catch(e){$('mountStatus').textContent=e.message;}$('mountSelect').blur();};
// Where the command goes. The camera computer only records the request; a separate sender
// (the PX4 simulator on the PC, or a flight-controller bridge) does the sending and says so.
function sendError(t){$('sendStatus').dataset.error=t;setTimeout(()=>{if($('sendStatus').dataset.error===t)delete $('sendStatus').dataset.error;},6000);}
async function askSend(target){
  if(target==='fc_hold'&&!confirm('Start the zero-setpoint stream to the flight-controller bridge\'s endpoint?\n\nThis lets the flight controller enter and stay in offboard mode, holding still. It must be the only offboard publisher. The status line says whether the bridge is on a simulator or a real flight controller.'))return;
  if(target==='fc'&&!confirm('Send the follow command through the flight-controller bridge?\n\nThe vehicle will turn and move toward the selected person if it is armed and in offboard mode. Props off or restrained unless this flight is authorised.'))return;
  try{const r=await fetch('/api/transmit?target='+target,{method:'POST'}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.reason||'Refused');delete $('sendStatus').dataset.error;}catch(e){sendError(e.message.charAt(0).toUpperCase()+e.message.slice(1));}
}
$('sendOff').onclick=()=>askSend('off');$('sendSim').onclick=()=>askSend('sim');$('sendFcHold').onclick=()=>askSend('fc_hold');$('sendFc').onclick=()=>askSend('fc');
function renderSend(){
  $('sendRow').hidden=!O;if(!O)return;const t=O.send;
  for(const [id,k,l] of [['sendOff','off',null],['sendSim','sim','sim'],['sendFcHold','fc_hold','fc'],['sendFc','fc','fc']]){const b=$(id);b.className=t.request===k?'selected':'';
    b.title=l===null||t.available[l]?'':'Not running: nothing is listening for this on the camera computer';}
  $('sendOff').textContent=t.stream===true?'Follow off (zero stream continues)':'Nowhere (shadow)';
  $('sendStatus').textContent=$('sendStatus').dataset.error||t.status;
}
$('carryRecord').onclick=async()=>{try{const r=await fetch('/api/onboard_record?label=carry&seconds=120',{method:'POST'}),j=await r.json();$('mountStatus').textContent=r.ok&&j.ok?'Recording to '+j.path:(j.reason||'Recording refused');}catch(e){$('mountStatus').textContent=e.message;}};
// Camera on the vehicle: one picture of the command the camera computer would give. Seen from
// above, vehicle at the bottom pointing up. Nothing here is a position; only the command.
function drawOnboard(g,w,h){
  const cx=w/2,cy=h*.74,col=O.valid?'#6de3e1':'#ffbf69',len=h*.42;
  g.strokeStyle='#28344880';g.lineWidth=1;for(let r=1;r<=3;r++){g.beginPath();g.arc(cx,cy,len*r/3,Math.PI,2*Math.PI);g.stroke();}
  g.beginPath();g.moveTo(cx-len,cy);g.lineTo(cx+len,cy);g.stroke();
  // forward / back arrow
  const f=O.forwardFrac*len*.9;g.strokeStyle=O.forward>=0?col:'#ffbf69';g.fillStyle=g.strokeStyle;g.lineWidth=5;
  if(Math.abs(f)>2){g.beginPath();g.moveTo(cx,cy);g.lineTo(cx,cy-f);g.stroke();const s=Math.sign(f);g.beginPath();g.moveTo(cx,cy-f-9*s);g.lineTo(cx-8,cy-f+3*s);g.lineTo(cx+8,cy-f+3*s);g.fill();}
  // turn arc
  const a=O.yawFrac*Math.PI/2.4;g.strokeStyle=col;g.lineWidth=4;
  if(Math.abs(a)>.02){const r=38,start=-Math.PI/2,end=start+a;g.beginPath();g.arc(cx,cy,r,Math.min(start,end),Math.max(start,end));g.stroke();const ex=cx+r*Math.cos(end),ey=cy+r*Math.sin(end),t=end+Math.sign(a)*Math.PI/2;g.fillStyle=col;g.beginPath();g.moveTo(ex+9*Math.cos(t),ey+9*Math.sin(t));g.lineTo(ex+6*Math.cos(end),ey+6*Math.sin(end));g.lineTo(ex-6*Math.cos(end),ey-6*Math.sin(end));g.fill();}
  // the selected person, seen from the vehicle: side from the picture, distance only if stereo gives one
  if(O.offset!==null){const b=O.offset*Math.PI/5,r=O.range!==null?Math.max(.18,Math.min(1,O.range/6))*len:len*.66,px=cx+r*Math.sin(b),py=cy-r*Math.cos(b);
    g.strokeStyle='#ff6b8b66';g.lineWidth=1;g.setLineDash([4,4]);g.beginPath();g.moveTo(cx,cy);g.lineTo(px,py);g.stroke();g.setLineDash([]);
    g.fillStyle='#ff6b8b';g.beginPath();g.arc(px,py,7,0,2*Math.PI);g.fill();g.font='11px sans-serif';g.textAlign='center';g.fillText(O.range!==null?O.range.toFixed(1)+' m':'person',px,py-12);}
  // vehicle
  g.save();g.translate(cx,cy);g.fillStyle='#edf3fa';g.beginPath();g.moveTo(0,-13);g.lineTo(10,10);g.lineTo(0,5);g.lineTo(-10,10);g.closePath();g.fill();g.restore();
  g.textAlign='center';g.fillStyle=col;g.font='700 15px sans-serif';g.fillText(O.valid?O.turn.toUpperCase():'HOLD · ALL SPEEDS ZERO',cx,26);
  g.fillStyle='#afbed1';g.font='11px sans-serif';
  g.fillText(O.valid?(O.range!==null?'Distance '+O.range.toFixed(2)+' m · wanted gap '+(O.gap??'—')+' m · '+O.source:'Forward speed from: '+O.source):O.reason,cx,46);
  g.fillStyle='#ffbf69';g.font='600 11px sans-serif';g.fillStyle=O.send.real?'#ff6b8b':O.send.sending?'#6de3e1':'#ffbf69';g.fillText(O.send.sending?O.send.banner+' · COMPUTED ON THE PI':O.send.request==='off'?'SHADOW · NOT SENT':O.send.banner.toUpperCase(),cx,h-34);g.textAlign='start';
}
function drawMap(o,now){const {g,w,h}=canvas('map');
  if(O){drawOnboard(g,w,h);return;}
  // One vehicle: the PX4 lab's while linked, otherwise this page's illustration. Never both.
  const veh=V?V.vehicle:{x:F.x,y:F.y,heading:F.heading},tp=V?V.target:o.point,alt=V?V.altitude:F.altitude,fitted=!!V||stereoFollowing();
  // Metric camera-origin views zoom to where the action is (the vehicle may stand off behind the camera).
  const fit=fitted?mapFit.update([veh,tp],now):null;
  const minY=fit?fit.minY:0,maxY=fit?fit.maxY:(cal?cal.depth+2:8),maxX=fit?fit.halfX*2:(cal?Math.max(4,cal.width+2):9),scale=Math.min((w-65)/maxX,(h-65)/(maxY-minY));
  const pt=(x,y)=>[w/2+x*scale,h-28-(y-minY)*scale];g.lineWidth=1;g.strokeStyle='#293b5080';
  const gridWidth=(w-45)/scale;
  for(let x=-Math.ceil(gridWidth/2);x<=gridWidth/2;x++){g.beginPath();g.moveTo(...pt(x,minY));g.lineTo(...pt(x,maxY));g.stroke();}
  for(let y=minY;y<=maxY;y++){g.beginPath();g.moveTo(...pt(-gridWidth/2,y));g.lineTo(...pt(gridWidth/2,y));g.stroke();}
  g.strokeStyle='#ffbf6955';g.setLineDash([6,7]);{const limit=V?V.applied.limit:F.settings.radius;if(limit!==null){g.beginPath();g.arc(...pt(0,V?.kind==='pi_onboard'?0:.8),limit*scale,0,Math.PI*2);g.stroke();}}g.setLineDash([]);
  if(cal&&!fitted){const [x,y]=pt(-cal.width/2,cal.depth+1);g.fillStyle='#6de3e108';g.fillRect(x,y,cal.width*scale,cal.depth*scale);g.strokeStyle='#6de3e155';g.strokeRect(x,y,cal.width*scale,cal.depth*scale);}
  else if(V?.kind!=='pi_onboard'){const [cx,cy]=pt(0,0);const grad=g.createRadialGradient(cx,cy,0,cx,cy,scale*7);grad.addColorStop(0,'#4a749c26');grad.addColorStop(1,'#4a749c00');g.fillStyle=grad;g.beginPath();g.moveTo(cx,cy);g.lineTo(...pt(-3.6,7));g.lineTo(...pt(3.6,7));g.closePath();g.fill();}
  if(now-lastTrail>100){lastTrail=now;if(veh)trail.push({x:veh.x,y:veh.y,t:now});if(tp)targetTrail.push({x:tp.x,y:tp.y,t:now});}trail=trail.filter(p=>now-p.t<6000);targetTrail=targetTrail.filter(p=>now-p.t<6000);
  function path(arr,color){if(arr.length<2)return;g.strokeStyle=color;g.lineWidth=2;g.beginPath();arr.forEach((p,i)=>{const a=pt(p.x,p.y);i?g.lineTo(...a):g.moveTo(...a);});g.stroke();}path(trail,'#6de3e166');path(targetTrail,'#ed1c2455');
  const t=tp||(targetTrail.length?targetTrail[targetTrail.length-1]:null);
  if(t){const [x,y]=pt(t.x,t.y);g.fillStyle=tp?'#ed1c241b':'#ffbf6914';g.strokeStyle=tp?'#ed1c2480':'#ffbf6955';g.beginPath();g.ellipse(x,y,Math.min(scale*.42,30),Math.min(scale*.62,44),0,0,Math.PI*2);g.fill();g.stroke();g.fillStyle=tp?'#ed1c24':'#ffbf69';g.beginPath();g.arc(x,y,5,0,Math.PI*2);g.fill();g.font='11px sans-serif';g.fillText(tp?'#'+(V?V.targetId:F.id):'LAST OBSERVATION',x+10,y-9);
    if(veh){g.strokeStyle='#8fa4bb66';g.setLineDash([4,6]);g.beginPath();g.moveTo(...pt(veh.x,veh.y));g.lineTo(x,y);g.stroke();g.setLineDash([]);}}
  if(t&&(V?V.mode==='CIRCLE':F.flightStyle==='circle')){g.strokeStyle='#6de3e155';g.setLineDash([5,5]);g.beginPath();g.arc(...pt(t.x,t.y),(V?(V.applied.gap??0):(F.orbitRadius??F.settings.gap))*scale,0,Math.PI*2);g.stroke();g.setLineDash([]);}
  const [cx,cy]=pt(0,0);g.fillStyle='#8399b3';g.fillRect(cx-6,cy-4,12,7);g.font='9px sans-serif';g.fillText(V?.kind==='pi_onboard'?'HOME':V?(V.kind==='mono_estimate'?'CAMERA (SCHEMATIC)':'CAMERA (ASSUMED POSITION)'):cal&&!fitted?'FLOOR ORIGIN':(stereoFollowing()?'CAMERA':'CAMERA (ILLUSTRATIVE)'),cx+12,cy+3);
  if(!veh){g.fillStyle='#ffbf69';g.font='600 13px sans-serif';g.textAlign='center';g.fillText(V&&V.contact?'PX4 TELEMETRY NOT FRESH · VEHICLE POSITION UNKNOWN':'NO CONTACT WITH THE PX4 LAB · VEHICLE STATE UNKNOWN',w/2,h/2);g.textAlign='start';return;}
  const [x,y]=pt(veh.x,veh.y),lift=alt??0;g.fillStyle='#6de3e10a';g.strokeStyle='#6de3e133';g.lineWidth=1;g.beginPath();g.arc(x,y,26,0,Math.PI*2);g.fill();g.stroke();g.fillStyle='#afbed1';g.font='11px sans-serif';g.fillText((alt===null?'—':alt.toFixed(1)+' m')+(V?' · PX4 SIM':' SIM'),x+22,y+22);g.save();g.translate(x,y);g.scale(1+lift*.12,1+lift*.12);g.rotate(veh.heading);g.shadowColor='#6de3e1';g.shadowBlur=reduced?0:14;g.strokeStyle='#b8ffff';g.lineWidth=2.2;g.beginPath();g.moveTo(-10,-10);g.lineTo(10,10);g.moveTo(10,-10);g.lineTo(-10,10);g.stroke();
  for(const [a,b]of[[-10,-10],[10,-10],[-10,10],[10,10]]){g.beginPath();g.arc(a,b,6,0,Math.PI*2);g.stroke();if(!reduced&&lift>0.05){g.save();g.translate(a,b);g.rotate(now*.025);g.beginPath();g.moveTo(-4,0);g.lineTo(4,0);g.stroke();g.restore();}}
  g.fillStyle='#fff';g.beginPath();g.moveTo(0,-18);g.lineTo(-4,-10);g.lineTo(4,-10);g.closePath();g.fill();g.restore();
}
let last=performance.now();
function tick(now){requestAnimationFrame(tick);if(now-lastPaint<32)return;const dt=Math.min((now-last)/1000,.05);last=now;lastPaint=now;
  if(typeof labFrame==='function')labFrame(now);
  // While linked the illustration stays on the ground: gestures or old state must not fly it unseen.
  if((V||O)&&F.airState!=='GROUNDED')F.ground();
  updatePositionSource();const useStereo=stereoFollowing();
  const o=F.step(dt,now,(t,s)=>useStereo?stereoRange.point(t,s,now):Garuda.project(t,s,cal));
  // Assert this display's target on the camera when it changes. This does not wait for a
  // position: with stereo follow the position needs the camera's selection, so waiting would
  // deadlock after a return under a new track ID.
  {const req=targetSync.next(S,F.id,now);if(req)sendTargetSync(req);}
  updateUI(o,now);drawOverlay();drawMap(o,now);drawGimbal(dt);
}requestAnimationFrame(tick);

// Stereo depth map inset: fetched only while switched on (it makes the Pi send and colour depth frames).
// A request that stalls, or a camera that stops, hides the picture instead of leaving it frozen.
(function(){const btn=$('depthButton'),img=$('depthPip'),gate=new Garuda.LiveGate(2500,2000);let timer=null,on=false,pending=null;
 function tick(){const now=Date.now();if(btn.hidden||gate.state(now,false)!=='live')img.hidden=true;if(btn.hidden)return;
  if(pending&&gate.timedOut(now)){pending.onload=pending.onerror=null;pending.src='';pending=null;}
  const token=gate.begin(now);if(token===null)return;const n=new Image();pending=n;
  n.onload=()=>{if(gate.ok(token,Date.now())){img.src=n.src;img.hidden=!on;}if(pending===n)pending=null;};
  n.onerror=()=>{gate.fail(token);img.hidden=true;if(pending===n)pending=null;};
  n.src='/stereo_depth.jpg?t='+now;}
 btn.onclick=()=>{on=!on;btn.classList.toggle('active',on);btn.textContent=on?'Hide depth map':'Depth map';
  if(on){tick();timer=setInterval(tick,330);}else{clearInterval(timer);timer=null;img.hidden=true;}};
})();
// The 3-D point cloud lives on its own page so it can use the whole window.
$('cloudButton').onclick=()=>window.open('/demo/cloud','garuda-cloud');
