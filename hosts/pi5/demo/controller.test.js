const {test}=require('node:test');
const assert=require('node:assert/strict');
const {recognise,validPeople,Flight,project,distance,stereo,stereoPoint,StereoRange,PositionSource,LiveGate,LiveDisplay,decodeCloud,cloudPoints,renderCloud,homography}=require('./controller');
function airborne(){const f=new Flight();f.airState='AIRBORNE';f.altitude=1.5;return f;}
function snapshot(seq=1){return {age_ms:10,frame:{width:1280,height:720,seq,epoch:'camera-a'},lock:{id:7},distance:{valid:true,est_m:3},tracks:[{id:7,label:'person',x:700,y:150,w:150,h:400,conf:.9,coasting:false}]};}
function following(){const f=airborne();f.receive(snapshot(),0);f.select(7,0);for(let n=0;n<=30;n++){f.receive(snapshot(n+2),n*50);f.step(.05,n*50,(t,s)=>project(t,s,null));}assert.equal(f.mode,'FOLLOW');return f;}
test('occlusion brakes to zero and holds instead of chasing last goal',()=>{
  const f=following();const s=snapshot(100);s.tracks[0].coasting=true;
  let prior=Math.hypot(f.vx,f.vy);for(let n=0;n<50;n++){f.receive({...s,frame:{...s.frame,seq:100+n}},1600+n*50);f.step(.05,1600+n*50,project);const speed=Math.hypot(f.vx,f.vy);assert.ok(speed<=prior+1e-10);prior=speed;}
  assert.equal(f.mode,'HOLD');assert.equal(prior,0);const y=f.y;f.step(.05,4200,project);assert.equal(f.y,y);
});
test('fresh SSE with old observations is not fresh perception',()=>{const f=following();f.receive({...snapshot(100),age_ms:60000},1600);f.step(.05,1600,project);assert.equal(f.mode,'BRAKE');assert.match(f.reason,/stale/);});
test('unchanged frame sequence is caught even if age is falsely fresh',()=>{const f=following();f.receive(snapshot(32),3000);f.step(.05,3000,project);assert.match(f.reason,/stale/);});
test('event connection loss is caught independently',()=>{const f=following();f.step(.05,3000,project);assert.match(f.reason,/Connection lost/);});
test('without an appearance reference a different person is never selected',()=>{const f=following();const s=snapshot(100);s.tracks[0].id=8;f.receive(s,1600);f.step(.05,1600,project);assert.equal(f.id,7);assert.match(f.reason,/select again/);});
test('camera restart cannot reuse an old track id',()=>{const f=following();const s=snapshot(100);s.frame.epoch='camera-b';f.receive(s,1600);f.step(.05,1600,project);assert.equal(f.requiresSelection,true);});
test('same coasting identity must stabilize before following resumes',()=>{const f=following();const s=snapshot(100);s.tracks[0].coasting=true;f.receive(s,1600);f.step(.05,1600,project);f.receive(snapshot(101),1700);f.step(.05,1700,project);assert.equal(f.mode,'ACQUIRE');for(let i=0;i<13;i++){f.receive(snapshot(102+i),1750+i*50);f.step(.05,1750+i*50,project);}assert.equal(f.mode,'FOLLOW');});
test('fault test and operator pause both brake despite valid live data',()=>{for(const field of ['fault','paused']){const f=following();f[field]=true;f.receive(snapshot(100),1600);f.step(.05,1600,project);assert.equal(f.mode,'BRAKE');}});
test('STOP gesture pauses only the selected subject when enabled',()=>{const f=following();f.gesturesEnabled=true;const s=snapshot(100);s.tracks[0].gesture={name:'stop',held_ms:1000};f.receive(s,1600);f.step(.05,1600,project);assert.equal(f.paused,true);});
test('bad, stale and other-person ranges never display',()=>{for(const r of [-1.35,0,Infinity,NaN,40])assert.equal(distance({...snapshot(),distance:{valid:true,est_m:r}},7),null);assert.equal(distance(snapshot(),8),null);assert.equal(distance({...snapshot(),distance:{valid:false,est_m:3}},7),null);});
test('schematic position ignores unreliable model range',()=>{const s=snapshot(),a=project(s.tracks[0],s);s.distance.est_m=-123;assert.deepEqual(project(s.tracks[0],s),a);assert.equal(a.metric,false);});
test('floor calibration maps corners and rejects invisible or out-of-zone feet',()=>{const p=[{x:.1,y:.9},{x:.9,y:.9},{x:.7,y:.2},{x:.3,y:.2}],cal=homography(p,4,5),s=snapshot();p.forEach((q,i)=>{const t={x:q.x*1280-10,y:q.y*720-100,w:20,h:100};const v=project(t,s,cal);assert.ok(v);assert.ok(Math.abs(v.x-[-2,2,2,-2][i])<1e-7);assert.ok(Math.abs(v.y-[1,1,6,6][i])<1e-7);});assert.equal(project({x:1,y:1,w:10,h:10},s,cal),null);assert.equal(project({x:500,y:500,w:100,h:220},s,cal),null);});
test('degenerate and crossed calibrations are rejected',()=>{assert.throws(()=>homography([{x:0,y:0},{x:1,y:1},{x:2,y:2},{x:3,y:3}],4,4));assert.throws(()=>homography([{x:0,y:0},{x:1,y:1},{x:0,y:1},{x:1,y:0}],4,4));});

const appearance=(i=0)=>{const a=Array(128).fill(0);a[i]=Math.SQRT1_2;a[64+i]=Math.SQRT1_2;return a;};
function remembered(){const f=airborne(),s=snapshot();s.tracks[0].appearance=appearance();f.receive(s,0);f.select(7,0);return f;}
function returning(seq=2){const s=snapshot(seq);s.tracks[0].id=9;s.tracks[0].appearance=appearance();return s;}
test('returning appearance reacquires a new ID after multi-frame dwell',()=>{const f=remembered();for(let i=0;i<20;i++){f.receive(returning(i+2),1000+i*100);f.step(.05,1000+i*100,project);}assert.equal(f.id,9);assert.equal(f.mode,'FOLLOW');assert.match(f.reason,/reacquired/);});
test('a different outfit cannot take the lock',()=>{const f=remembered();for(let i=0;i<20;i++){const s=returning(i+2);s.tracks[0].appearance=appearance(12);f.receive(s,1000+i*100);f.step(.05,1000+i*100,project);}assert.equal(f.id,7);assert.equal(f.mode,'HOLD');});
test('two similar-looking people keep the vehicle holding',()=>{const f=remembered();for(let i=0;i<20;i++){const s=returning(i+2);s.tracks.push({...s.tracks[0],id:10});f.receive(s,1000+i*100);f.step(.05,1000+i*100,project);}assert.equal(f.id,7);assert.match(f.reason,/Similar-looking/);});
test('same frame cannot satisfy reacquisition dwell',()=>{const f=remembered();for(let i=0;i<15;i++){f.receive(returning(),1000+i*100);f.step(.05,1000+i*100,project);}assert.equal(f.id,7);});
test('return window expires',()=>{const f=remembered();f.receive(returning(),31000);f.step(.05,31000,project);assert.equal(f.id,7);assert.match(f.reason,/expired/);});
test('a known bystander cannot be reacquired even with matching clothes',()=>{const f=airborne(),s=snapshot();s.tracks[0].appearance=appearance();s.tracks.push({...s.tracks[0],id:9});f.receive(s,0);f.select(7,0);for(let i=0;i<15;i++){f.receive(returning(i+2),1000+i*100);f.step(.05,1000+i*100,project);}assert.equal(f.id,7);});
test('camera epoch changes still require explicit selection with matching appearance',()=>{const f=remembered(),s=returning();s.frame.epoch='new-camera';f.receive(s,1000);f.step(.05,1000,project);assert.equal(f.id,7);assert.equal(f.requiresSelection,true);});

test('configuration clamps limits and ignores invalid inputs',()=>{const f=airborne();f.configure({gap:99,radius:-1,speed:NaN});assert.deepEqual(f.settings,{gap:5,radius:1,speed:2.2});});
test('requested gap changes settled separation',()=>{for(const gap of [1,3]){const f=remembered();f.configure({gap});for(let i=0;i<400;i++){f.receive(snapshot(i+2),i*50);f.step(.05,i*50,()=>({x:0,y:6,metric:true}));}assert.ok(Math.abs(6-f.y-gap)<.05);}});
test('maximum travel boundary and speed cap bound simulated movement',()=>{const f=remembered();f.configure({radius:1,speed:.5,gap:.5});for(let i=0;i<300;i++){f.receive(snapshot(i+2),i*50);f.step(.05,i*50,()=>({x:8,y:8,metric:true}));assert.ok(Math.hypot(f.x,f.y-.8)<=1+1e-9);assert.ok(Math.hypot(f.vx,f.vy)<=.5+1e-9);}});
test('selected person can stop resume and change side; bystander cannot',()=>{const f=remembered();f.gesturesEnabled=true;const send=(name,id=7,seq=2)=>{const s=snapshot(seq);s.tracks[0].id=id;s.tracks[0].gesture={name,held_ms:1200};f.receive(s,seq*100);};send('stop',9);assert.equal(f.paused,false);send('stop',7,3);assert.equal(f.paused,true);send('follow_me',7,4);assert.equal(f.paused,false);send('point_left',7,5);assert.equal(f.side,-1);send('point_right',7,6);assert.equal(f.side,1);});
test('gesture commands are ignored when disabled or stale',()=>{const f=remembered();const s=snapshot(2);s.tracks[0].gesture={name:'stop'};f.receive(s,100);assert.equal(f.paused,false);f.gesturesEnabled=true;s.frame.seq=3;s.age_ms=2000;f.receive(s,200);assert.equal(f.paused,false);});
test('one unambiguous follow gesture can select an idle target',()=>{const f=airborne();f.gesturesEnabled=true;const s=snapshot();s.tracks[0].gesture={name:'follow_me',held_ms:1000};f.receive(s,100);assert.equal(f.id,7);});
function enrolled(){const f=remembered();f.setFaceMode(true);assert.equal(f.enroll('Asha'),true);for(let i=0;i<3;i++){const s=snapshot(i+2);s.tracks[0].face={embedding:appearance(),sample:'face'+i,age_ms:100};f.receive(s,1000+i*800);}assert.equal(f.matcher.faceProfiles.length,3);return f;}
test('face enrollment requires three distinct fresh samples',()=>{const f=remembered();f.setFaceMode(true);f.enroll('Asha');for(let i=0;i<8;i++){const s=snapshot(i+2);s.tracks[0].face={embedding:appearance(),sample:'same',age_ms:100};f.receive(s,1000+i*100);}assert.equal(f.matcher.faceProfiles.length,0);assert.equal(f.faceSamples.length,1);});
test('face-confirmed return never falls back to matching clothes',()=>{const f=enrolled();for(let i=0;i<20;i++){const s=returning(i+10);f.receive(s,4000+i*100);f.step(.05,4000+i*100,project);}assert.equal(f.id,7);});
test('two distinct matching face samples allow new-ID reacquisition',()=>{const f=enrolled();for(let i=0;i<25;i++){const s=returning(i+10);s.tracks[0].face={embedding:appearance(),sample:'return'+Math.floor(i/8),age_ms:100};f.receive(s,4000+i*100);f.step(.05,4000+i*100,project);}assert.equal(f.id,9);assert.equal(f.mode,'FOLLOW');});
test('a nonmatching face or repeated face sample cannot reacquire',()=>{for(const wrong of [true,false]){const f=enrolled();for(let i=0;i<20;i++){const s=returning(i+10);s.tracks[0].face={embedding:appearance(wrong?4:0),sample:wrong?'frame'+i:'same',age_ms:100};f.receive(s,4000+i*100);f.step(.05,4000+i*100,project);}assert.equal(f.id,7);}});
test('releasing target clears enrolled face reference',()=>{const f=enrolled();f.reset();assert.equal(f.matcher.faceProfiles.length,0);assert.equal(f.faceSamples.length,0);});
test('grounded selection with gestures disabled never launches',()=>{const f=new Flight();f.receive(snapshot(),0);f.select(7,0);for(let i=0;i<60;i++){const s=snapshot(i+2);s.tracks[0].gesture={name:'follow_me'};f.receive(s,i*50);f.step(.05,i*50,project);}assert.equal(f.airState,'GROUNDED');assert.equal(f.altitude,0);assert.equal(f.x,0);assert.equal(f.y,.8);assert.equal(f.mode,'GROUND');});
test('selected gestures launch orbit hold follow and land without interrupting descent',()=>{const f=new Flight();f.gesturesEnabled=true;let seq=0;const send=(name,id=7)=>{const s=snapshot(++seq);s.tracks[0].id=id;s.tracks[0].gesture={name};f.receive(s,seq*50);};send('follow_me');assert.equal(f.id,7);assert.equal(f.airState,'TAKING OFF');for(let i=0;i<50;i++){send('follow_me');f.step(.05,seq*50,project);}assert.equal(f.airState,'AIRBORNE');send('circle');assert.equal(f.flightStyle,'circle');send('stop');assert.equal(f.paused,true);send('follow_me');assert.equal(f.flightStyle,'follow');assert.equal(f.paused,false);send('land',9);assert.equal(f.airState,'AIRBORNE');send('land');assert.equal(f.airState,'LANDING');send('follow_me');assert.equal(f.airState,'LANDING');for(let i=0;i<100;i++){send('follow_me');f.step(.05,seq*50,project);}assert.equal(f.airState,'GROUNDED');send(null);send('follow_me');assert.equal(f.airState,'TAKING OFF');});
test('stale land and bystander circle commands are ignored',()=>{const f=remembered();f.gesturesEnabled=true;let s=snapshot(2);s.tracks[0].id=9;s.tracks[0].gesture={name:'circle'};f.receive(s,100);assert.equal(f.flightStyle,'follow');s=snapshot(3);s.age_ms=2000;s.tracks[0].gesture={name:'land'};f.receive(s,150);assert.equal(f.airState,'AIRBORNE');});
test('takeoff climbs vertically and landing works without camera data',()=>{const f=new Flight();assert.equal(f.takeoff(),true);assert.equal(f.takeoff(),false);for(let i=0;i<50;i++)f.step(.05,i*50,project);assert.equal(f.airState,'AIRBORNE');assert.equal(f.altitude,1.5);assert.equal(f.x,0);assert.equal(f.y,.8);f.vx=1;f.land();const h=f.altitude;f.step(.05,2600,project);assert.equal(f.altitude,h);for(let i=0;i<100;i++)f.step(.05,2700+i*50,project);assert.equal(f.airState,'GROUNDED');assert.equal(f.altitude,0);assert.equal(f.vx,0);assert.equal(f.mode,'GROUND');});
test('circle traverses target quadrants and brakes when target disappears',()=>{const f=remembered();f.configure({gap:1,radius:8});f.setFlightStyle('circle');const quadrants=new Set();for(let i=0;i<900;i++){f.receive(snapshot(i+2),i*50);f.step(.05,i*50,()=>({x:0,y:3}));if(i>200)quadrants.add([Math.sign(f.x),Math.sign(f.y-3)].join(','));}assert.equal(f.mode,'CIRCLE');assert.equal(quadrants.size,4);const angle=f.orbitAngle;for(let i=0;i<60;i++){const s=snapshot(1000+i);s.tracks=[];f.receive(s,46000+i*50);f.step(.05,46000+i*50,project);}assert.equal(f.mode,'HOLD');assert.equal(Math.hypot(f.vx,f.vy),0);assert.equal(f.orbitAngle,angle);});
test('circle outside travel boundary holds rather than clipping orbit',()=>{const f=remembered();f.configure({gap:3,radius:2});f.setFlightStyle('circle');for(let i=0;i<60;i++){f.receive(snapshot(i+2),i*50);f.step(.05,i*50,()=>({x:0,y:3}));}assert.equal(f.mode,'HOLD');assert.match(f.reason,/Circle exceeds/);assert.equal(f.x,0);assert.equal(f.y,.8);});
test('landing interrupts circle and fresh targets cannot restart flight',()=>{const f=remembered();f.setFlightStyle('circle');f.land();for(let i=0;i<160;i++){f.receive(snapshot(i+2),i*50);f.step(.05,i*50,()=>({x:0,y:3}));}assert.equal(f.airState,'GROUNDED');assert.equal(f.mode,'GROUND');assert.equal(f.altitude,0);});
test('large requested orbit fits travel boundary and still circles',()=>{const f=remembered();f.configure({gap:4.6,radius:6});f.setFlightStyle('circle');const quadrants=new Set();for(let i=0;i<1600;i++){f.receive(snapshot(i+2),i*50);f.step(.05,i*50,()=>({x:0,y:3}));assert.ok(Math.hypot(f.x,f.y-.8)<=6+1e-9);if(i>300)quadrants.add([Math.sign(f.x),Math.sign(f.y-3)].join(','));}assert.equal(f.mode,'CIRCLE');assert.ok(f.orbitRadius<4.6);assert.equal(quadrants.size,4);});

// ---- OAK stereo readout ----
const okStereo={status:'ok',reason:'ok',z_m:2.934,coverage:.3,support:.832};
const withStereo=(st,lock=7)=>({...snapshot(),lock:{id:lock},stereo:st});
test('stereo line is hidden when stereo is not enabled',()=>{assert.equal(stereo(snapshot(),7,true),null);assert.equal(stereo(null,7,true),null);});
test('stereo shows a number only for ok, labelled unvalidated with both scores',()=>{assert.equal(stereo(withStereo(okStereo),7,true),'Stereo depth: 2.93 m (unvalidated) · valid pixels 30% · surface share 83%');});
test('stereo never shows a number for a stale state',()=>{assert.equal(stereo(withStereo(okStereo),7,false),'Stereo depth: unavailable (camera data is stale)');});
test('stereo never shows another target\'s depth',()=>{for(const id of [8,null,undefined])assert.match(stereo(withStereo(okStereo),id,true),/unavailable \(this display's target is not the camera's locked target\)/);});
test('stereo rejects impossible ok values',()=>{for(const z of [0,-1,31,NaN,Infinity,null,'2.5'])assert.equal(stereo(withStereo({...okStereo,z_m:z}),7,true),'Stereo depth: unavailable (value out of range)');});
test('ambiguous lists the competing surfaces and no single value',()=>{const t=stereo(withStereo({status:'ambiguous',reason:'competing_depth_clusters',z_m:null,candidates:[{z_m:2.5,support:.5},{z_m:1.5,support:.5}]}),7,true);assert.equal(t,'Stereo depth: ambiguous (unvalidated surfaces at 1.50 m and 2.50 m)');});
test('every non-ok status explains itself without a number',()=>{for(const [status,reason,why] of [['unavailable','unsupported_target_class','selected target is not a person'],['unavailable','target_coasting','target not currently seen'],['invalid','cluster_too_spread','depth too scattered'],['unavailable','stale_state','camera data is stale'],['invalid','something_new','no measurement']]){const t=stereo(withStereo({status,reason,z_m:2.5,coverage:1,support:1}),7,true);assert.equal(t,'Stereo depth: unavailable ('+why+')');assert.doesNotMatch(t,/\d m/);}});
test('missing scores render as a dash, not NaN',()=>{assert.equal(stereo(withStereo({status:'ok',z_m:1.5}),7,true),'Stereo depth: 1.50 m (unvalidated) · valid pixels — · surface share —');});

// ---- follow position from stereo depth ----
const FX=640/Math.tan(66.5275*Math.PI/360);
const trk=(cx,id=7)=>({id,label:'person',x:cx-75,y:150,w:150,h:400,conf:.9,coasting:false});
const sst=(z,status='ok',reason='ok',lock=7)=>({...snapshot(),lock:{id:lock},stereo:{status,reason,z_m:z}});
test('stereo point: straight ahead is x=0, y=z',()=>{const p=stereoPoint(trk(640),sst(3));assert.ok(Math.abs(p.x)<1e-9);assert.equal(p.y,3);assert.equal(p.metric,true);assert.equal(p.source,'stereo');});
test('stereo point: sideways offset scales with depth and pixel offset',()=>{const p=stereoPoint(trk(640+FX/2),sst(4));assert.ok(Math.abs(p.x-2)<1e-9);const q=stereoPoint(trk(640-FX/2),sst(2));assert.ok(Math.abs(q.x+1)<1e-9);});
test('stereo point: image edge is half the field of view',()=>{const p=stereoPoint(trk(1280),sst(1));assert.ok(Math.abs(Math.atan2(p.x,p.y)*180/Math.PI-66.5275/2)<1e-6);});
test('stereo point: none unless ok, finite, in range and for the locked track',()=>{for(const s of [sst(3,'ambiguous','competing_depth_clusters'),sst(3,'invalid','cluster_too_spread'),sst(3,'unavailable','stale_state'),sst(0),sst(-1),sst(31),sst(NaN),sst(Infinity),sst(null),sst('3'),sst(3,'ok','ok',8),snapshot()])assert.equal(stereoPoint(trk(640),s),null);});
test('stereo range holds the last depth briefly with the current bearing, then gives no position',()=>{const r=new StereoRange(700);assert.equal(r.point(trk(640),sst(3),0).y,3);
 const h=r.point(trk(640+FX/2),sst(null,'ambiguous','competing_depth_clusters'),400);assert.equal(h.y,3);assert.equal(h.held,true);assert.ok(Math.abs(h.x-1.5)<1e-9);
 const gone=r.point(trk(640),sst(null,'ambiguous','competing_depth_clusters'),701);assert.equal(gone.x,undefined);assert.match(gone.reason,/^Stereo distance unavailable/);
 assert.equal(r.point(trk(640),sst(null,'invalid','cluster_too_spread'),705).x,undefined);});
test('stereo range does not carry depth to another track or another camera epoch',()=>{const r=new StereoRange(700);r.point(trk(640),sst(3),0);assert.equal(r.point(trk(640,9),{...sst(null,'unavailable','no_target',9)},100).x,undefined);
 const r2=new StereoRange(700);r2.point(trk(640),sst(3),0);const other={...sst(null,'invalid','cluster_too_spread'),frame:{width:1280,height:720,seq:9,epoch:'camera-b'}};assert.equal(r2.point(trk(640),other,100).x,undefined);});
test('stereo range never falls back to the model estimate or the schematic view',()=>{const r=new StereoRange(700);const s={...sst(null,'unavailable','unsupported_target_class'),distance:{valid:true,est_m:3}};const p=r.point(trk(640),s,0);assert.equal(p.x,undefined);assert.equal(p.reason,'Stereo distance unavailable • selected target is not a person');});
test('stereo range says why when stereo is off or the camera lock differs',()=>{const r=new StereoRange();assert.match(r.point(trk(640),snapshot(),0).reason,/stereo is off/);assert.match(r.point(trk(640),sst(3,'ok','ok',8),0).reason,/another target/);});
test('follow uses the stereo point and brakes with the stereo reason when it is gone',()=>{const f=airborne(),r=new StereoRange(700);const step=(s,now)=>{f.receive(s,now);return f.step(.05,now,(t,q)=>r.point(t,q,now));};
 const mk=(n,z,status='ok')=>({...sst(z,status,status==='ok'?'ok':'cluster_too_spread'),frame:{width:1280,height:720,seq:n,epoch:'camera-a'}});
 f.receive(mk(1,4),0);f.select(7,0);let o;for(let n=0;n<=30;n++)o=step(mk(n+2,4),n*50);assert.equal(f.mode,'FOLLOW');assert.equal(o.point.source,'stereo');assert.equal(o.point.y,4);
 for(let n=31;n<=60;n++)o=step(mk(n+2,null,'invalid'),n*50);assert.ok(['BRAKE','HOLD'].includes(f.mode));assert.match(f.reason,/^Stereo distance unavailable/);});

// ---- 3-D point cloud ----
function cloudBuf(w,h,depths,bgr){const b=new ArrayBuffer(8+w*h*5),d=new DataView(b);'GCL1'.split('').forEach((c,i)=>d.setUint8(i,c.charCodeAt(0)));d.setUint16(4,w,true);d.setUint16(6,h,true);
 for(let i=0;i<w*h;i++)d.setUint16(8+i*2,depths[i]||0,true);const u=new Uint8Array(b,8+w*h*2);if(bgr)u.set(bgr);return b;}
test('cloud payload decodes; anything else is refused',()=>{const c=decodeCloud(cloudBuf(3,2,[1000,0,2000,0,0,3000]));assert.equal(c.w,3);assert.equal(c.h,2);assert.deepEqual(Array.from(c.depth),[1000,0,2000,0,0,3000]);
 const good=cloudBuf(3,2,[]);assert.equal(decodeCloud(good.slice(0,good.byteLength-1)),null);assert.equal(decodeCloud(new ArrayBuffer(4)),null);const bad=cloudBuf(3,2,[]);new Uint8Array(bad)[0]=88;assert.equal(decodeCloud(bad),null);assert.equal(decodeCloud('x'),null);});
test('cloud points: the pixel at the scaled principal point is on the axis; zeros and far values are skipped',()=>{const w=4,h=2,d=new Array(w*h).fill(0);d[1*w+2]=2000;d[0]=9000;const p=cloudPoints(decodeCloud(cloudBuf(w,h,d)));assert.equal(p.n,1);assert.ok(Math.abs(p.xyz[0])<1e-6&&Math.abs(p.xyz[1])<1e-6);assert.equal(p.xyz[2],2);});
test('cloud points: grid index maps to image pixel index*step (origin 0), so the last column is short of the half field of view',()=>{const w=160,h=1,d=new Array(w).fill(0);d[159]=1000;d[0]=1000;const p=cloudPoints(decodeCloud(cloudBuf(w,h,d)));const half=Math.tan(66.5275*Math.PI/360);
 assert.ok(Math.abs(p.xyz[0]/p.xyz[2]-(-80/80)*half)<1e-6);assert.ok(Math.abs(p.xyz[3]/p.xyz[5]-(79/80)*half)<1e-6);});
test('cloud points carry their own pixel colour as RGB',()=>{const bgr=new Uint8Array(3);bgr.set([10,20,30]);const p=cloudPoints(decodeCloud(cloudBuf(1,1,[1500],bgr)));assert.deepEqual(Array.from(p.rgb),[30,20,10]);});
test('render: a point straight ahead lands at the centre, front view',()=>{const W=32,H=32,out=new Uint8ClampedArray(W*H*4);const pts={n:1,xyz:new Float32Array([0,0,2]),rgb:new Uint8Array([200,100,50])};const n=renderCloud(pts,out,W,H,{yaw:0,pitch:0,zoom:1});assert.equal(n,1);const q=(16*W+16)*4;assert.deepEqual([out[q],out[q+1],out[q+2],out[q+3]],[200,100,50,255]);});
test('render: the nearer point wins a shared pixel',()=>{const W=32,H=32,out=new Uint8ClampedArray(W*H*4);const pts={n:3,xyz:new Float32Array([0,0,3, 0,0,1, 0,0,2]),rgb:new Uint8Array([1,1,1, 9,9,9, 5,5,5])};renderCloud(pts,out,W,H,{yaw:0,pitch:0,zoom:1});const q=(16*W+16)*4;assert.equal(out[q],9);});
test('render: empty cloud draws nothing and reports zero',()=>{const out=new Uint8ClampedArray(16*16*4);assert.equal(renderCloud(null,out,16,16,{yaw:0,pitch:0,zoom:1}),0);assert.equal(renderCloud({n:0,xyz:new Float32Array(0),rgb:new Uint8Array(0)},out,16,16,{yaw:0,pitch:0,zoom:1}),0);assert.equal(out[3],255);});
test('render: rotating the view moves an off-pivot point sideways',()=>{const W=64,H=64,pts={n:2,xyz:new Float32Array([0,0,2, 0,0,3]),rgb:new Uint8Array([255,0,0, 0,255,0])};const a=new Uint8ClampedArray(W*H*4),b=new Uint8ClampedArray(W*H*4);renderCloud(pts,a,W,H,{yaw:0,pitch:0,zoom:1.5});renderCloud(pts,b,W,H,{yaw:0.6,pitch:0,zoom:1.5});assert.notDeepEqual(Array.from(a),Array.from(b));});
test('render: splats grow with canvas size so a fronto-parallel grid has no gaps',()=>{const w=40,h=24,d=new Array(w*h).fill(2000);const p=cloudPoints(decodeCloud(cloudBuf(w,h,d)));assert.ok(p.f>0);
 const W=400,H=240,out=new Uint8ClampedArray(W*H*4);renderCloud(p,out,W,H,{yaw:0,pitch:0,zoom:1});let lit=0,total=0;for(let y=H/2-40;y<H/2+40;y++)for(let x=W/2-60;x<W/2+60;x++){total++;if(out[(y*W+x)*4]||out[(y*W+x)*4+1]||out[(y*W+x)*4+2])lit++;}
 const bgr=new Uint8Array(w*h*3).fill(200);const q=cloudPoints(decodeCloud(cloudBuf(w,h,d,bgr)));const out2=new Uint8ClampedArray(W*H*4);renderCloud(q,out2,W,H,{yaw:0,pitch:0,zoom:1});let lit2=0;for(let y=H/2-40;y<H/2+40;y++)for(let x=W/2-60;x<W/2+60;x++)if(out2[(y*W+x)*4])lit2++;assert.equal(lit2,total);});

// ---- review astra-0022 ----
const seqd=(n,z,status='ok')=>({...sst(z,status,status==='ok'?'ok':'competing_depth_clusters'),frame:{width:1280,height:720,seq:n,epoch:'camera-a'}});
test('R1: repeating the same good snapshot does not renew the stereo hold',()=>{const r=new StereoRange(700);assert.equal(r.point(trk(640),seqd(1,3),0).y,3);assert.equal(r.point(trk(640),seqd(1,3),650).y,3);
 const late=r.point(trk(640),seqd(2,null,'ambiguous'),1300);assert.equal(late.x,undefined);assert.match(late.reason,/^Stereo distance unavailable/);});
test('R1: a new good snapshot does renew it, and held points report their age',()=>{const r=new StereoRange(700);r.point(trk(640),seqd(1,3),0);r.point(trk(640),seqd(2,3.1),600);
 const h=r.point(trk(640),seqd(3,null,'ambiguous'),1100);assert.equal(h.held,true);assert.equal(h.y,3.1);assert.equal(h.age_ms,500);assert.equal(r.point(trk(640),seqd(4,null,'ambiguous'),1301).x,undefined);});
test('R1: the same good snapshot shown for longer than the hold is itself stale',()=>{const r=new StereoRange(700);r.point(trk(640),seqd(1,3),0);const p=r.point(trk(640),seqd(1,3),800);assert.equal(p.x,undefined);assert.match(p.reason,/stale/);});
test('R1: a held depth is visible in the vehicle reason, not reported as fresh',()=>{const f=airborne(),r=new StereoRange(700);const step=(s,now)=>{f.receive(s,now);return f.step(.05,now,(t,q)=>r.point(t,q,now));};
 f.receive(seqd(1,4),0);f.select(7,0);for(let n=0;n<=30;n++)step(seqd(n+2,4),n*50);assert.equal(f.mode,'FOLLOW');assert.doesNotMatch(f.reason,/held/);
 step(seqd(40,null,'ambiguous'),1550);assert.equal(f.mode,'FOLLOW');assert.match(f.reason,/^Stereo depth held • last value first seen here \d+ ms ago/);});
test('R3: selected stereo stays selected when the stereo block disappears',()=>{const p=new PositionSource();assert.deepEqual(p.update(false,true),{mode:'classic',changed:false});assert.deepEqual(p.update(true,true),{mode:'stereo',changed:true});
 assert.deepEqual(p.update(false,true),{mode:'stereo',changed:false});assert.deepEqual(p.update(false,true),{mode:'stereo',changed:false});
 const r=new StereoRange();assert.match(r.point(trk(640),snapshot(),0).reason,/stereo is off/);});
test('R3: explicit operator toggles change the source and say so',()=>{const p=new PositionSource();p.update(true,true);assert.deepEqual(p.update(true,false),{mode:'classic',changed:true});assert.deepEqual(p.update(false,false),{mode:'classic',changed:false});assert.deepEqual(p.update(false,true),{mode:'stereo',changed:true});});
test('R3: never selects stereo before it has been seen',()=>{const p=new PositionSource();for(let i=0;i<3;i++)assert.equal(p.update(false,true).mode,'classic');});
test('R3: rebase puts the simulated vehicle home at rest and keeps the selection',()=>{const f=following();f.x=3;f.y=5;f.vx=1;f.vy=-1;f.heading=1;const id=f.id;f.rebase();assert.deepEqual([f.x,f.y,f.vx,f.vy,f.heading],[0,.8,0,0,0]);assert.equal(f.id,id);assert.equal(f.goodSince,null);});
test('R2: a request that never settles frees the gate after its deadline and goes stale',()=>{const g=new LiveGate(2500,3000);const a=g.begin(0);assert.ok(a);assert.equal(g.ok(a,100),true);assert.equal(g.state(200,false),'live');
 const b=g.begin(500);assert.ok(b);assert.equal(g.begin(1000),null);assert.equal(g.timedOut(2999),false);assert.equal(g.timedOut(3000),true);assert.equal(g.state(3101,false),'stale');
 const c=g.begin(3000);assert.ok(c&&c!==b);assert.equal(g.ok(b,3200),false);assert.equal(g.state(3200,false),'stale');assert.equal(g.ok(c,3300),true);assert.equal(g.state(3300,false),'live');});
test('R2: failures and malformed payloads never count as fresh',()=>{const g=new LiveGate(2500,3000);assert.equal(g.state(0,false),'waiting');const a=g.begin(0);g.fail(a);assert.equal(g.state(10,false),'waiting');assert.ok(g.begin(20));assert.equal(decodeCloud(new ArrayBuffer(7)),null);});
test('R2: pause is distinct from data loss',()=>{const g=new LiveGate(2500,3000);g.ok(g.begin(0),0);assert.equal(g.state(99999,true),'paused');assert.equal(g.state(99999,false),'stale');});

// ---- review astra-0023 ----
test('gate: a reply after the deadline is refused even if no newer request was started',()=>{const g=new LiveGate(2500,3000);const t1=g.begin(0);assert.equal(g.ok(t1,4000),false);assert.equal(g.state(4000,false),'waiting');assert.equal(g.lastOk,null);
 const h=new LiveGate(2500,3000);const a=h.begin(0);assert.equal(h.ok(a,2499),true);const b=h.begin(3000);assert.equal(h.ok(b,5500),false);assert.equal(h.lastOk,2499);});
test('gate: success after a failure of the same request is refused',()=>{const g=new LiveGate(2500,3000);const t1=g.begin(0);g.fail(t1);assert.equal(g.ok(t1,100),false);assert.equal(g.state(100,false),'waiting');});
test('gate: a cancelled request cannot succeed',()=>{const g=new LiveGate(2500,3000);const t1=g.begin(0);g.cancel();assert.equal(g.ok(t1,100),false);assert.ok(g.begin(101));});
test('display: resume clears what was shown at once, and only a request started after resume repopulates it',()=>{const g=new LiveGate(2500,3000),d=new LiveDisplay(g);
 assert.equal(d.success(g.begin(0),100,'A'),true);assert.deepEqual(d.watch(200),{state:'live',clear:false,text:'A'});
 d.pause();assert.equal(d.watch(300).text,'Paused · showing A');const inFlight=g.begin(400);
 d.resume();const w=d.watch(500);assert.equal(w.clear,true);assert.equal(w.state,'waiting');assert.equal(w.text,'Connecting…');
 assert.equal(d.success(inFlight,600,'OLD'),false);                       // requested before resume: refused
 for(const t of [1000,5000,60000]){const x=d.watch(t);assert.equal(x.clear,false);assert.notEqual(x.state,'live');assert.equal(d.has,false);}   // never completes: stays empty
 assert.equal(d.success(g.begin(60001),60100,'B'),true);assert.deepEqual(d.watch(60200),{state:'live',clear:false,text:'B'});});
test('display: replies while paused never replace the frozen frame',()=>{const g=new LiveGate(2500,3000),d=new LiveDisplay(g);d.success(g.begin(0),10,'A');const t=g.begin(20);d.pause();assert.equal(d.success(t,30,'B'),false);assert.equal(d.watch(40).text,'Paused · showing A');});
test('display: stale data is cleared once and the last failure stays visible',()=>{const g=new LiveGate(2500,3000),d=new LiveDisplay(g);d.success(g.begin(0),10,'A');d.failure(g.begin(100),'stereo is switched off on the camera');
 assert.deepEqual(d.watch(200),{state:'live',clear:false,text:'A'});const w=d.watch(4000);assert.equal(w.clear,true);assert.equal(w.text,'No live stereo data — last frame cleared · stereo is switched off on the camera');assert.equal(d.watch(4100).clear,false);
 d.pause();assert.equal(d.watch(4200).text,'Paused · nothing to show · stereo is switched off on the camera');});
test('display: a failure message is never presented as the frame being shown',()=>{const g=new LiveGate(2500,3000),d=new LiveDisplay(g);d.failure(g.begin(0),'unreadable data from the camera');d.pause();assert.doesNotMatch(d.watch(10).text,/showing/);});

test('GCL2 binds exact intrinsics to sampled grid and rejects invalid geometry',()=>{
 const geometry={schema_version:1,epoch:'run',calibration_id:'cal',width:4,height:4,fx:4,fy:8,cx:1,cy:2,distortion:'none',source:'sdk-transformation-metadata',reference_frame:'unverified',verified:false};
 function wire(g){const h=Buffer.from(JSON.stringify({geometry:g,step:2,width:2,height:2}));const b=Buffer.alloc(8+h.length+20);b.write('GCL2');b.writeUInt32LE(h.length,4);h.copy(b,8);for(let i=0;i<4;i++)b.writeUInt16LE(2000,8+h.length+i*2);return b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength);}
 const c=Garuda.decodeCloud(wire(geometry));assert.ok(c);const p=Garuda.cloudPoints(c);
 assert.equal(p.xyz[0],-.5);assert.equal(p.xyz[1],-.5);assert.equal(p.xyz[2],2);
 assert.equal(Garuda.decodeCloud(wire({...geometry,fx:0})),null);
});
test('camera label names the three bench cameras; stereo only on the OAK',()=>{const {cameraLabel}=require('./controller');
 assert.equal(cameraLabel('csi',false),'CM3 CAMERA · CSI');assert.equal(cameraLabel('/dev/video8',false),'USB WEBCAM');
 assert.equal(cameraLabel('oak-rgb',true),'OAK-D PRO · STEREO');assert.equal(cameraLabel('oak-rgb',false),'OAK-D PRO · COLOUR ONLY');
 assert.equal(cameraLabel(undefined,false),'CAMERA');assert.equal(cameraLabel('other',true),'CAMERA');});
test('host-stereo geometry is accepted for the stereo follow point; an unknown source is not',()=>{
 const g={schema_version:1,epoch:'run',calibration_id:'cal',width:1280,height:720,fx:800,fy:800,cx:640,cy:360,distortion:'none',source:'host-opencv-factory-calibration',reference_frame:'unverified',verified:false};
 const s={frame:{width:1280,height:720},lock:{id:7},stereo:{status:'ok',z_m:2,geometry:g,calibration_id:'cal',pair:{epoch:'run'}}},t={id:7,x:940,y:100,w:200,h:500};
 const p=stereoPoint(t,s);assert.equal(p.y,2);assert.ok(Math.abs(p.x-2*(1040-640)/800)<1e-9);
 assert.equal(stereoPoint(t,{...s,stereo:{...s.stereo,geometry:{...g,source:'guess'}}}),null);});
test('host box-stereo refusals are worded for the operator',()=>{const s={lock:{id:7},stereo:{status:'unavailable',reason:'frame_not_in_history'}};
 assert.match(stereo(s,7,true),/frame too old for stereo/);});

// Owner report 2026-11-14: with stereo follow, raising the gap to 5 m did not move the vehicle
// further away. The gap was capped to the space between camera and person (about 0.4 m when
// standing 1.2 m from the camera).
function stereoFollow(z,settings){const f=airborne(),r=new StereoRange(700);f.configure(settings);
 const mk=n=>({...sst(z),frame:{width:1280,height:720,seq:n,epoch:'camera-a'}});
 f.receive(mk(1),0);f.select(7,0);let o;for(let n=0;n<=400;n++){f.receive(mk(n+2),n*50);o=f.step(.05,n*50,(t,q)=>r.point(t,q,n*50));}return {f,o};}
test('stereo follow honours the requested gap, standing off behind the camera when the person is close',()=>{
 const {f,o}=stereoFollow(1.2,{gap:5,radius:12});assert.equal(f.mode,'FOLLOW');
 const sep=Math.hypot(o.point.x-f.x,o.point.y-f.y);assert.ok(Math.abs(sep-5)<.15,'separation '+sep);
 assert.ok(f.y<0,'vehicle is behind the camera');assert.equal(f.gapLimited,null);
 const near=stereoFollow(1.2,{gap:2,radius:12});assert.ok(Math.abs(Math.hypot(near.o.point.x-near.f.x,near.o.point.y-near.f.y)-2)<.15);});
test('stereo follow: the travel limit still bounds the vehicle and says so',()=>{
 const {f,o}=stereoFollow(1.2,{gap:5,radius:3});
 assert.ok(Math.hypot(f.x,f.y-.8)<=3.001);assert.ok(Math.hypot(o.point.x-f.x,o.point.y-f.y)<4.6);assert.equal(f.reason,'Following within your travel limit');});
test('schematic follow keeps the vehicle in front of the camera and reports a limited gap',()=>{
 const f=airborne();f.configure({gap:5,radius:12});const s=n=>({...snapshot(n)});
 f.receive(s(1),0);f.select(7,0);let o;for(let n=0;n<=400;n++){f.receive(s(n+2),n*50);o=f.step(.05,n*50,(t,q)=>project(t,q,null));}
 assert.equal(f.mode,'FOLLOW');assert.ok(f.y>=0);assert.ok(f.gapLimited!==null&&f.gapLimited<5);assert.match(f.reason,/gap limited to \d\.\d m by the space in front of the camera/);});
test('display: a detector restart needs reselection; an unconfirmed camera identity holds',()=>{
 const f=airborne();const snap=(n,over={})=>({...snapshot(n),stats:{instance:'proc'},...over});
 f.receive(snap(1),0);f.select(7,0);for(let n=0;n<=30;n++){f.receive(snap(n+2),n*50);f.step(.05,n*50,(t,s)=>project(t,s,null));}
 assert.equal(f.mode,'FOLLOW');
 f.receive(snap(40,{stats:{instance:'proc',identity:{target:7,confirmed:false}}}),1600);let o=f.step(.05,1600,(t,s)=>project(t,s,null));
 assert.equal(o.point,undefined);assert.match(f.reason,/checking that this is still the selected person/);
 f.receive(snap(41,{stats:{instance:'restarted'}}),1650);o=f.step(.05,1650,(t,s)=>project(t,s,null));
 assert.equal(f.requiresSelection,true);assert.match(f.reason,/select again/);});

test('enrolment needs a name and stores the person under it',()=>{const f=remembered();f.setFaceMode(true);assert.equal(f.enroll(''),false);assert.equal(f.enroll('   '),false);assert.equal(f.enrolling,false);
  let saved=null;const g=enrolled();assert.deepEqual(g.people.map(p=>p.name),['Asha']);assert.equal(g.people[0].samples.length,3);assert.equal(g.nameOf(7),'Asha');assert.match(g.faceMessage,/Asha/);
  g.onPeople=p=>saved=p;g.forgetPeople();assert.deepEqual(saved,[]);assert.equal(g.nameOf(7),null);});
test('a known face is named on a new track, kept while turned away, and dropped for a different face',()=>{const f=enrolled();
  const s=returning(40);s.tracks[0].face={embedding:appearance(),sample:'n1',age_ms:100};f.receive(s,9000);assert.equal(f.nameOf(9),'Asha');
  const away=returning(41);f.receive(away,9100);assert.equal(f.nameOf(9),'Asha');
  const other=returning(42);other.tracks[0].face={embedding:appearance(4),sample:'n2',age_ms:100};f.receive(other,9200);assert.equal(f.nameOf(9),null);
  const stale=returning(43);stale.tracks[0].face={embedding:appearance(),sample:'n3',age_ms:5000};f.receive(stale,9300);assert.equal(f.nameOf(9),null);});
test('names are cleared when the camera session changes and when face mode is off',()=>{const f=enrolled();const s=returning(40);s.tracks[0].face={embedding:appearance(),sample:'n1',age_ms:100};f.receive(s,9000);assert.equal(f.nameOf(9),'Asha');
  const next=returning(41);next.frame.epoch='camera-b';f.receive(next,9100);assert.equal(f.nameOf(9),null);
  const g=enrolled();g.receive(s,9000);g.setFaceMode(false);g.receive(returning(41),9100);assert.equal(g.nameOf(9),null);});
test('two enrolled people who score alike give no name; re-enrolling a name replaces it',()=>{
  const people=validPeople([{name:'Asha',samples:[appearance()]},{name:'Ravi',samples:[appearance()]}]);assert.equal(recognise(people,{embedding:appearance(),age_ms:100}),null);
  assert.equal(recognise(validPeople([{name:'Asha',samples:[appearance()]},{name:'Ravi',samples:[appearance(4)]}]),{embedding:appearance(4),age_ms:100}),'Ravi');
  assert.deepEqual(validPeople([{name:'<b>x</b>\n'+'y'.repeat(60),samples:[appearance()]},{name:'',samples:[appearance()]},{name:'Bad',samples:[[1,2]]},null]).map(p=>p.name.length),[24]);
  const f=enrolled();f.select?.(7,3000);f.enroll('asha');for(let i=0;i<3;i++){const s=snapshot(i+60);s.tracks[0].face={embedding:appearance(),sample:'again'+i,age_ms:100};f.receive(s,12000+i*800);}assert.deepEqual(f.people.map(p=>p.name),['asha']);});
