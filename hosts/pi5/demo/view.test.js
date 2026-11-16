const {test}=require('node:test');
const assert=require('node:assert/strict');
const {depthViews,PositionSource,capabilities,rangeReadout,identityStatus,labView,labControl,labSourceFor,Unlinker,MapFit}=require('./controller');

const oak=(extra={})=>({frame:{source:'oak-rgb'},lock:{id:7},stereo:{status:'ok',z_m:1.052},stats:{capabilities:{camera:'oak',distance_source:'stereo',depth_map:true,cloud:true}},...extra});
const cm3=(extra={})=>({frame:{source:'csi'},lock:{id:7},distance:{valid:true,est_m:2.44},stats:{capabilities:{camera:'cm3',distance_source:'mono_estimate',depth_map:false,cloud:false}},...extra});

test('capabilities come from the detector and are never upgraded to stereo for a one-lens camera',()=>{
  assert.deepEqual(capabilities(oak()),{camera:'oak',distance:'stereo',depthMap:true,cloud:true,explicit:true});
  assert.deepEqual(capabilities(cm3()),{camera:'cm3',distance:'mono_estimate',depthMap:false,cloud:false,explicit:true});
  // A detector that wrongly claims stereo for the CM3 gets no distance and no depth views.
  const lie=cm3({stats:{capabilities:{distance_source:'stereo',depth_map:true,cloud:true}}});
  assert.deepEqual(capabilities(lie),{camera:'cm3',distance:'none',depthMap:false,cloud:false,explicit:true});
  assert.equal(capabilities({frame:{source:'/dev/video8'},stats:{capabilities:{distance_source:'none'}}}).camera,'usb');
});
test('an older detector without the block is inferred from what it publishes',()=>{
  assert.equal(capabilities({frame:{source:'oak-rgb'},stereo:{status:'no_target'}}).distance,'stereo');
  assert.equal(capabilities({frame:{source:'oak-rgb'}}).distance,'none');
  assert.equal(capabilities({frame:{source:'csi'},distance:{valid:false}}).distance,'mono_estimate');
  assert.equal(capabilities({frame:{source:'csi'},stereo:{status:'ok'}}).distance,'none');
  assert.deepEqual(capabilities(null),{camera:'unknown',distance:'none',depthMap:false,cloud:false,explicit:false});
});
test('the distance readout names its source and its worth, and shows a number only when valid',()=>{
  const s=oak();
  assert.deepEqual(rangeReadout(s,7,true,capabilities(s)),{value:'1.05 m',ok:true,source:'STEREO DEPTH',badge:'UNVALIDATED'});
  const m=cm3();
  assert.deepEqual(rangeReadout(m,7,true,capabilities(m)),{value:'2.4 m',ok:true,source:'MONO ESTIMATE',badge:'NOT A MEASUREMENT'});
  assert.equal(rangeReadout(s,null,true,capabilities(s)),null);
  assert.equal(rangeReadout({frame:{source:'csi'},stats:{capabilities:{distance_source:'none'}}},7,true,{distance:'none'}),null);
  for(const bad of [oak({lock:{id:9}}),oak({stereo:{status:'low_coverage',z_m:1}}),oak({stereo:{status:'ok',z_m:NaN}}),oak({stereo:undefined})])
    assert.equal(rangeReadout(bad,7,true,capabilities(oak())).ok,false);
  assert.equal(rangeReadout(s,7,false,capabilities(s)).value,'—');
  // A stereo camera never shows the mono network's number in the stereo readout.
  const both=oak({stereo:{status:'no_depth'},distance:{valid:true,est_m:3}});
  assert.equal(rangeReadout(both,7,true,capabilities(both)).value,'—');
  assert.equal(rangeReadout(cm3({distance:{valid:false,est_m:2}}),7,true,capabilities(cm3())).ok,false);
});
test('identity status is shown only for this display\'s person and never defaults to confirmed',()=>{
  const st=i=>({frame:{seq:5,epoch:'cam:0'},stats:{instance:'proc',identity:{frame_seq:5,frame_epoch:'cam:0',instance:'proc',...i}}});
  assert.equal(identityStatus({stats:{}},7),null);
  assert.equal(identityStatus(st({target:9,state:'tracking',confirmed:true}),7),null);
  assert.equal(identityStatus(st({target:7,state:'tracking',confirmed:true}),null),null);
  assert.deepEqual(identityStatus(st({target:7,state:'tracking',confirmed:true}),7),{level:'ok',text:'SAME PERSON · CONFIRMED'});
  assert.match(identityStatus(st({target:7,state:'tracking',confirmed:true,rebound_from:3}),7).text,/RE-FOUND \(WAS #3\)/);
  assert.equal(identityStatus(st({target:7,state:'tracking',confirmed:false}),7).level,'warn');
  // A confirmed decision that belongs to another frame, camera run or detector process is not shown green.
  for(const stale of [{frame_seq:4},{frame_epoch:'cam:1'},{instance:'old'},{instance:undefined}])
    assert.deepEqual(identityStatus(st({target:7,state:'tracking',confirmed:true,...stale}),7),{level:'warn',text:'CHECKING IDENTITY'});
  assert.equal(identityStatus({stats:{identity:{target:7,state:'tracking',confirmed:true}}},7).level,'warn');
  assert.equal(identityStatus(st({target:7,state:'verifying',confirmed:false}),7).text,'CHECKING IDENTITY');
  assert.equal(identityStatus(st({target:7,state:'searching'}),7).level,'warn');
  assert.equal(identityStatus(st({target:7,state:'ambiguous'}),7).level,'bad');
  assert.equal(identityStatus(st({target:7,state:'expired'}),7).level,'bad');
});

const lab=(extra={})=>({phase:'FLYING',armed:true,behavior:'follow',position:[1.5,0.4,-1.5],telemetry_fresh:true,attitude:[1,0,0,0],altitude:1.5,
  source_kind:'fixed_stereo',accept_preview_stereo_in_simulation:true,target_id:7,target:[3.2,-0.5],lock_state:'LOCKED',requires_reselection:false,reason:'Following',error:'',...extra});
test('link off: the page illustration is the vehicle',()=>{assert.equal(labView(false,lab()),null);});
test('linked: the PX4 lab is the one vehicle, placed in the camera-origin map frame',()=>{
  const v=labView(true,lab());
  assert.equal(v.contact,true);assert.equal(v.mode,'FOLLOW');assert.equal(v.airborne,true);
  assert.deepEqual(v.vehicle,{x:0.4,y:2.3,heading:0});assert.deepEqual(v.target,{x:-0.5,y:4});
  assert.match(v.detail,/PX4 SITL · 1\.5 m · stereo distance \(unvalidated\)/);
  assert.deepEqual(v.can,{takeoff:false,follow:true,circle:true,land:true,hold:true,auto:true});
  const h=labView(true,lab({home:[10,20],position:[11,21,-1],target:[13,20]}));
  assert.deepEqual(h.vehicle,{x:1,y:1.8,heading:0});assert.deepEqual(h.target,{x:0,y:3.8});
  assert.ok(Math.abs(labView(true,lab({attitude:[Math.SQRT1_2,0,0,Math.SQRT1_2]})).vehicle.heading-Math.PI/2)<1e-9);
});
test('linked on the ground: only take off is offered',()=>{
  const v=labView(true,lab({phase:'GROUND',armed:false,behavior:'hold',altitude:0}));
  assert.equal(v.mode,'GROUND');assert.equal(v.airborne,false);
  assert.deepEqual(v.can,{takeoff:true,follow:false,circle:false,land:false,hold:false,auto:true});
});
test('linked: phases between ground and flying are not shown as ground, and an armed GROUND phase is not grounded',()=>{
  for(const phase of ['PRESTREAM','REQUESTING'])assert.equal(labView(true,lab({phase})).mode,'TAKEOFF');
  // A fault, or a phase this page does not know, is shown under its own name and never as take-off.
  for(const phase of ['FAULT','SOMETHING_NEW']){const f=labView(true,lab({phase}));assert.equal(f.mode,phase);assert.equal(f.style,'HOLD');assert.deepEqual([f.can.takeoff,f.can.follow,f.can.land,f.can.hold],[false,false,true,true]);}
  assert.equal(labView(true,lab({phase:'LANDING'})).mode,'LAND');
  const armed=labView(true,lab({phase:'GROUND',armed:true}));
  assert.equal(armed.airborne,true);assert.equal(armed.can.takeoff,false);assert.equal(armed.can.land,true);
  // Armed state unknown is not "disarmed".
  assert.equal(labView(true,lab({phase:'GROUND',armed:undefined})).can.takeoff,false);
});
test('linked without contact: no vehicle, no movement commands, land and hold stay available',()=>{
  for(const none of [null,undefined,{},{phase:5}]){const v=labView(true,none);
    assert.equal(v.contact,false);assert.equal(v.mode,'NO CONTACT');assert.equal(v.vehicle,null);assert.equal(v.target,null);
    assert.deepEqual(v.can,{takeoff:false,follow:false,circle:false,land:true,hold:true,auto:false});}
});
test('linked: stale telemetry or a lab that needs reselection shows no vehicle position / no target and offers no follow',()=>{
  assert.equal(labView(true,lab({telemetry_fresh:false})).vehicle,null);
  // Missing is not fresh: no position and no movement commands, on the ground or in the air.
  for(const tf of [false,undefined,null,'yes']){const v=labView(true,lab({telemetry_fresh:tf})),g=labView(true,lab({telemetry_fresh:tf,phase:'GROUND',armed:false}));
    assert.equal(v.vehicle,null);assert.equal(v.fresh,false);assert.deepEqual([v.can.follow,v.can.circle,g.can.takeoff],[false,false,false]);assert.deepEqual([v.can.land,v.can.hold],[true,true]);assert.match(v.reason,/telemetry is not fresh/);}
  assert.equal(labView(true,lab({position:null})).vehicle,null);
  const r=labView(true,lab({requires_reselection:true}));
  assert.equal(r.target,null);assert.equal(r.can.follow,false);assert.equal(r.can.circle,false);
  assert.equal(labView(true,lab({target_id:null,lock_state:'UNSELECTED',target:null})).can.follow,false);
  assert.match(labView(true,lab({accept_preview_stereo_in_simulation:false})).detail,/NOT accepted/);
  assert.match(labView(true,lab({source_kind:'pi'})).detail,/lab source: pi/);
});

test('unlink on the ground or with the link already off happens at once, with no command',()=>{
  const u=new Unlinker();
  assert.deepEqual(u.request(labView(true,lab({phase:'GROUND',armed:false})),0),{unlink:true,send:null});assert.equal(u.note,'');
});
test('unlink in the air asks for hold and waits for a state received after the request that reports hold',()=>{
  const u=new Unlinker();
  assert.deepEqual(u.request(labView(true,lab()),1000),{unlink:false,send:{command:'hold'}});assert.equal(u.pending,true);
  // Still following: stay linked.
  assert.equal(u.update(labView(true,lab()),1200,1200).unlink,false);
  // A hold state that was received BEFORE the request proves nothing.
  assert.equal(u.update(labView(true,lab({behavior:'hold'})),900,1300).unlink,false);
  assert.equal(u.update(labView(true,lab({behavior:'hold'})),1400,1400).unlink,true);assert.equal(u.pending,false);
  // Afterwards nothing more happens by itself.
  assert.deepEqual(u.update(labView(true,lab()),1500,1500),{unlink:false,send:null});
});
test('unlink that is not confirmed in time unlinks anyway and says it was not confirmed',()=>{
  const u=new Unlinker(4000);u.request(labView(true,lab()),0);
  assert.equal(u.update(labView(true,lab()),3000,3000).unlink,false);assert.equal(u.note,'');
  assert.equal(u.update(labView(true,lab()),4100,4100).unlink,true);
  assert.equal(u.pending,false);assert.match(u.note,/WITHOUT confirmation: hold was not confirmed.*in time/);
  // Losing contact while waiting is not a confirmation: the wait runs to its limit, then the same.
  const w=new Unlinker(4000);w.request(labView(true,lab()),0);
  assert.equal(w.update(labView(true,null),500,500).unlink,false);assert.equal(w.pending,true);
  assert.equal(w.update(labView(true,null),4100,4100).unlink,true);assert.match(w.note,/WITHOUT confirmation/);
});
test('unlink without contact or with stale telemetry always unlinks, and only an untouched link does so without a warning',()=>{
  const gone=labView(true,null);
  {const u=new Unlinker();assert.deepEqual(u.request(gone,0,{contacted:false}),{unlink:true,send:null});assert.match(u.note,/never answered/);assert.doesNotMatch(u.note,/WITHOUT/);}
  {const u=new Unlinker();assert.deepEqual(u.request(gone,0),{unlink:true,send:null});}
  // Any earlier contact or command, however the vehicle was last seen: unlinked, with the warning, on the first click.
  const u=new Unlinker();
  for(const t of [0,500,5000,60000]){assert.deepEqual(u.request(gone,t,{contacted:true}),{unlink:true,send:null});assert.equal(u.pending,false);assert.match(u.note,/WITHOUT the PX4 lab's confirmation \(no contact with the lab\).*state is unknown/);}
  // Contact but stale telemetry: hold is still sent, nothing is treated as confirmed, and it unlinks.
  const stale=labView(true,lab({telemetry_fresh:false,behavior:'hold'})),s=new Unlinker();
  for(const contacted of [false,true]){assert.deepEqual(s.request(stale,0,{contacted}),{unlink:true,send:{command:'hold'}});assert.match(s.note,/WITHOUT.*telemetry is not fresh/);assert.equal(s.pending,false);}
});
test('unlink with a take-off outstanding: a state that still says GROUND is not accepted as landed',()=>{
  const ground=labView(true,lab({phase:'GROUND',armed:false,behavior:'hold',altitude:0}));
  assert.deepEqual(new Unlinker().request(ground,0,{contacted:true,takeoffPending:false}),{unlink:true,send:null});
  const u=new Unlinker();
  assert.deepEqual(u.request(ground,0,{contacted:true,takeoffPending:true}),{unlink:false,send:{command:'hold'}});assert.equal(u.pending,true);
  assert.equal(u.update(ground,100,100,true).unlink,false);
  // Once the lab is known to have dealt with the take-off, the grounded state counts.
  assert.equal(u.update(ground,200,200,false).unlink,true);
});
test('a hold seen while telemetry is not fresh does not confirm an unlink',()=>{
  const u=new Unlinker();u.request(labView(true,lab()),0);
  assert.equal(u.update(labView(true,lab({behavior:'hold',telemetry_fresh:false})),200,200).unlink,false);assert.equal(u.pending,true);
  assert.equal(u.update(labView(true,lab({behavior:'hold'})),300,300).unlink,true);
});
test('a landing confirmed by a fresh grounded state also completes the unlink',()=>{
  const u=new Unlinker();u.request(labView(true,lab()),0);
  assert.equal(u.update(labView(true,lab({phase:'GROUND',armed:false,behavior:'hold'})),200,200).unlink,true);
});

test('map bounds grow at once, keep the camera in view, and shrink only after a quiet period',()=>{
  const m=new MapFit(3000);
  assert.deepEqual(m.update([{x:.1,y:1.05}],0),{halfX:2,maxY:3,minY:0});
  // The vehicle stands off behind the camera: the map opens that side immediately.
  assert.deepEqual(m.update([{x:.1,y:1.05},{x:0,y:-1.2}],100),{halfX:2,maxY:3,minY:-3});
  assert.deepEqual(m.update([{x:3.5,y:5.2},{x:0,y:-1.2}],200),{halfX:6,maxY:8,minY:-3});
  // Back near the camera: no shrink yet.
  assert.deepEqual(m.update([{x:.1,y:1}],1000),{halfX:6,maxY:8,minY:-3});
  assert.deepEqual(m.update([{x:.1,y:1}],3900),{halfX:6,maxY:8,minY:-3});
  assert.deepEqual(m.update([{x:.1,y:1}],4100),{halfX:2,maxY:2,minY:0});
});
test('map bounds do not flap when a point hovers around a step, and ignore bad points',()=>{
  const m=new MapFit(3000);m.update([{x:0,y:1.9}],0);
  assert.equal(m.update([{x:0,y:2.1}],100).maxY,4);
  assert.equal(m.update([{x:0,y:1.9}],200).maxY,4);
  assert.equal(m.update([{x:0,y:2.1}],2900).maxY,4);
  // The return above the step restarted the quiet period.
  assert.equal(m.update([{x:0,y:1.9}],3300).maxY,4);
  assert.equal(m.update([{x:0,y:1.9}],6400).maxY,3);
  assert.deepEqual(new MapFit().update([null,{x:NaN,y:2},{x:1e9,y:1e9}],0),{halfX:16,maxY:16,minY:0});
  assert.deepEqual(new MapFit().update([],0),{halfX:2,maxY:2,minY:0});
});

// ---- control lease, command acknowledgement, mono source (lab contract astra-0067/0068) ----
const ctl=(owner,extra={})=>({control:{owner,lease_ms:owner?2500:0,expired:false,last_command:null,...extra}});
test('control: this tab, another display, free, lapsed, and a lab without leases are told apart',()=>{
  assert.equal(labControl(lab(),'me'),'legacy');
  assert.equal(labControl(lab(ctl('me')),'me'),'mine');assert.equal(labControl(lab(ctl('them')),'me'),'other');
  assert.equal(labControl(lab(ctl(null)),'me'),'free');assert.equal(labControl(lab(ctl(null,{expired:true})),'me'),'expired');
  // No session of our own never matches an owner.
  assert.equal(labControl(lab(ctl('them')),null),'other');
});
test('another display in control: view only here, but land and hold stay available',()=>{
  const v=labView(true,lab(ctl('them')),'me');
  assert.equal(v.control,'other');assert.match(v.reason,/^View only · another display controls the PX4 lab/);
  assert.deepEqual(v.can,{takeoff:false,follow:false,circle:false,land:true,hold:true,auto:false});
  const g=labView(true,lab({...ctl('them'),phase:'GROUND',armed:false}),'me');assert.equal(g.can.takeoff,false);
});
test('lapsed control: the page says so, sends nothing unasked, and an explicit command may take control again',()=>{
  const v=labView(true,lab({...ctl(null,{expired:true}),behavior:'hold'}),'me');
  assert.equal(v.control,'expired');assert.match(v.reason,/control lapsed.*holding.*press a command/);
  assert.equal(v.can.auto,false);assert.equal(v.can.follow,true);assert.equal(v.can.hold,true);
  assert.equal(labView(true,lab(ctl('me')),'me').can.auto,true);assert.equal(labView(true,lab(ctl(null)),'me').can.auto,true);
});
test('lab source follows the camera: stereo for the OAK, the labelled estimate for one-lens cameras, never a fallback',()=>{
  assert.equal(labSourceFor(capabilities(oak())),'fixed_stereo');assert.equal(labSourceFor(capabilities(cm3())),'mono_estimate');
  assert.equal(labSourceFor({camera:'usb',distance:'mono_estimate'}),'mono_estimate');
  // OAK without stereo is NOT quietly driven by the one-lens estimate; unknown cameras drive nothing.
  assert.equal(labSourceFor({camera:'oak',distance:'mono_estimate'}),null);assert.equal(labSourceFor({camera:'oak',distance:'none'}),null);
  assert.equal(labSourceFor({camera:'cm3',distance:'none'}),null);assert.equal(labSourceFor({camera:'unknown',distance:'mono_estimate'}),null);
  assert.equal(labSourceFor({camera:'cm3',distance:'stereo'}),null);assert.equal(labSourceFor(null),null);
});
test('linked to the one-lens source: wording says schematic and not measured, and whether the lab accepts it',()=>{
  const v=labView(true,lab({source_kind:'mono_estimate',mono_estimate:{accepted:true}}));
  assert.equal(v.kind,'mono_estimate');assert.match(v.detail,/one-lens estimate · schematic, not measured/);assert.match(v.scale,/SCHEMATIC.*NOT MEASURED/);
  assert.doesNotMatch(v.detail+v.scale,/stereo/i);
  assert.match(labView(true,lab({source_kind:'mono_estimate',mono_estimate:{accepted:false}})).detail,/NOT accepted/);
  assert.match(labView(true,lab({source_kind:'mono_estimate'})).detail,/NOT accepted/);
  assert.match(labView(true,lab()).scale,/STEREO UNVALIDATED/);
});
test('unlink with command ids: only the lab applying THIS hold confirms it',()=>{
  const u=new Unlinker(),hold=id=>({...ctl('me',{last_command:{id,name:'hold',applied:true,error:null}}),behavior:'hold'});
  u.request(labView(true,lab(ctl('me')),'me'),1000);u.sent(true,'h1');
  // Holding because of some earlier command is not the confirmation.
  assert.equal(u.update(labView(true,lab(hold('older')),'me'),1200,1200).unlink,false);
  // Applied, but the state does not show hold yet.
  assert.equal(u.update(labView(true,lab({...hold('h1'),behavior:'follow'}),'me'),1300,1300).unlink,false);
  // Our hold was applied earlier and a later command id is now the last one: still confirmed.
  assert.equal(u.update(labView(true,lab(hold('later')),'me'),1400,1400).unlink,true);
});
test('unlink: a refused or undelivered hold ends the wait by unlinking with the reason',()=>{
  const u=new Unlinker();u.request(labView(true,lab(ctl('me')),'me'),0);u.sent(true,'h1');
  const refused={...ctl('me',{last_command:{id:'h1',name:'hold',applied:false,error:'nope'}}),behavior:'hold'};
  assert.equal(u.update(labView(true,lab(refused),'me'),100,100).unlink,true);assert.equal(u.pending,false);assert.match(u.note,/WITHOUT confirmation: the PX4 lab refused hold \(nope\)/);
  const w=new Unlinker();w.request(labView(true,lab(ctl('me')),'me'),0);w.sent(false,null);
  assert.equal(w.pending,true);                                     // resolved on the next state, not left waiting
  assert.equal(w.update(labView(true,lab({behavior:'hold'})),100,100).unlink,true);assert.equal(w.pending,false);assert.match(w.note,/WITHOUT confirmation: Hold could not be delivered/);
  // A new request starts clean.
  assert.deepEqual(w.request(labView(true,lab({phase:'GROUND',armed:false})),200),{unlink:true,send:null});assert.equal(w.note,'');
});
test('depth controls obey the camera as it is now: OAK then an explicit one-lens camera in the same page',()=>{
  const pos=new PositionSource();
  // On the OAK: stereo seen and latched for follow.
  pos.update(true,true);assert.equal(pos.seen,true);
  assert.deepEqual(depthViews(capabilities(oak()),pos.seen),{map:true,cloud:true,panel:true});
  // The device is switched to the CM3, which says so: no depth map, 3D view or stereo panel,
  // although this page still remembers stereo (that memory only keeps follow from falling back).
  pos.update(false,true);assert.equal(pos.seen,true);
  assert.deepEqual(depthViews(capabilities(cm3()),pos.seen),{map:false,cloud:false,panel:false});
  assert.deepEqual(depthViews(capabilities({frame:{source:'/dev/video0'},stats:{capabilities:{distance_source:'none'}}}),true),{map:false,cloud:false,panel:false});
  // An OAK that states it has no stereo right now is obeyed too.
  assert.deepEqual(depthViews(capabilities({frame:{source:'oak-rgb'},stats:{capabilities:{distance_source:'none'}}}),true),{map:false,cloud:false,panel:false});
  // Only a detector that says nothing about itself keeps the controls through a missing stereo block.
  assert.deepEqual(depthViews(capabilities({frame:{source:'oak-rgb'}}),true),{map:true,cloud:true,panel:true});
  assert.deepEqual(depthViews(capabilities({frame:{source:'oak-rgb'}}),false),{map:false,cloud:false,panel:false});
});
test('linked: the settings shown are the ones the lab reports it applies, not the page\'s requests',()=>{
  assert.deepEqual(labView(true,lab({gap:2,speed:1,limit:10})).applied,{gap:2,speed:1,limit:10});
  assert.deepEqual(labView(true,lab()).applied,{gap:null,speed:null,limit:null});
  assert.deepEqual(labView(true,lab({gap:'2',speed:NaN,limit:null})).applied,{gap:null,speed:null,limit:null});
  assert.deepEqual(labView(true,null).applied,{gap:null,speed:null,limit:null});
});

// --- camera on the vehicle (shadow mode) ------------------------------------------------
const {onboardView,mountOf}=require('./controller');
function veh(over={},cmd={}){const s=oak();s.frame.seq=9;s.frame.epoch='e';s.stats.instance='i';s.stats.capabilities.mount='vehicle';
  s.onboard={valid:true,reason:'tracking',yaw_rate:.25,forward_vel:.4,vertical_vel:0,target_id:7,range_m:4.2,range_source:'stereo',forward_reason:'ok',frame_seq:9,frame_epoch:'e',instance:'i',transmit:false,limits:{gap_m:2.5,max_yaw_rate:.5,max_forward_vel:.8,max_back_vel:.4},...cmd};return Object.assign(s,over);}
test('fixed mount: no on-vehicle view, lab source unchanged; vehicle mount: onboard simulator source',()=>{
  assert.equal(onboardView(oak(),true,80),null);assert.equal(labSourceFor(capabilities(oak())),'fixed_stereo');
  assert.equal(labSourceFor(capabilities(veh())),'pi_onboard');
  const m=cm3();m.stats.capabilities.mount='vehicle';assert.equal(labSourceFor(capabilities(m)),'pi_onboard');
  assert.deepEqual(mountOf(veh()),{mount:'vehicle',locked:false,recording:false});assert.equal(mountOf(null).mount,'fixed');});
test('on-vehicle view: a fresh command for the frame shown is described and marked not sent',()=>{
  const v=onboardView(veh(),true,80);assert.equal(v.mode,'FOLLOW');assert.equal(v.valid,true);assert.match(v.reason,/person #7/);
  assert.match(v.detail,/turn right 0\.25 rad\/s · forward 0\.40 m\/s · NOT SENT/);assert.equal(v.yawFrac,.5);assert.equal(v.forwardFrac,.5);assert.equal(v.range,4.2);
  const back=onboardView(veh({}, {yaw_rate:-.5,forward_vel:-.4}),true,80);assert.match(back.detail,/turn left 0\.50 rad\/s · back 0\.40 m\/s/);assert.equal(back.forwardFrac,-1);
  const noz=onboardView(veh({}, {forward_vel:0,range_m:null,forward_reason:'no stereo distance (cluster_too_spread)'}),true,80);
  assert.match(noz.detail,/no forward motion \(no stereo distance \(cluster_too_spread\)\)/);assert.equal(noz.range,null);});
test('on-vehicle view: anything doubtful is HOLD with zero speeds',()=>{
  for(const [s,fresh,why] of [[veh(),false,/stale/],[veh({onboard:undefined}),true,/No command/],[veh({}, {frame_seq:8}),true,/not for the frame/],[veh({}, {frame_epoch:'old'}),true,/not for the frame/],[veh({}, {instance:'other'}),true,/not for the frame/],
    [veh({}, {transmit:true}),true,/not marked as unsent/],[veh({}, {yaw_rate:NaN}),true,/Unusable/],[veh({}, {forward_vel:'1'}),true,/Unusable/],[veh({}, {valid:false,reason:'no person selected'}),true,/No person selected/]]){
    const v=onboardView(s,fresh,80);assert.equal(v.mode,'HOLD');assert.equal(v.yaw,0);assert.equal(v.forward,0);assert.equal(v.yawFrac,0);assert.equal(v.forwardFrac,0);assert.equal(v.range,null);assert.match(v.reason,why);assert.match(v.detail,/All speeds zero · (NOT SENT|SENDING STATE UNKNOWN)/);assert.equal(v.send.sending,false);}});
test('on-vehicle view: one-lens camera is worded as a size proxy, never a distance',()=>{const s=veh({}, {range_source:'box_height',range_m:null});const v=onboardView(s,true,80);assert.match(v.source,/box size \(a proxy, not a distance\)/);assert.equal(v.range,null);});
test('on-vehicle view: a frozen command expires at 400 ms on the page clock, whatever else stays fresh',()=>{
  const s=veh();for(const age of [0,80,399,400])assert.equal(onboardView(s,true,age).mode,'FOLLOW');
  for(const age of [401,650,5000,-1,NaN,undefined,null])assert.equal(onboardView(s,true,age).mode,'HOLD',String(age));
  const loose=veh({}, {limits:{gap_m:2.5,max_yaw_rate:.5,max_forward_vel:.8,max_back_vel:.4,max_age_ms:5000}});assert.equal(onboardView(loose,true,450).mode,'HOLD');   // a lax detector limit is not obeyed
  const tight=veh({}, {limits:{gap_m:2.5,max_yaw_rate:.5,max_forward_vel:.8,max_back_vel:.4,max_age_ms:200}});assert.equal(onboardView(tight,true,250).mode,'HOLD');assert.equal(onboardView(tight,true,150).mode,'FOLLOW');});

// --- where the on-vehicle command is sent ------------------------------------------------
const {transmitView}=require('./controller');
const tx=(request,l={})=>({request,generation:1,reason:'requested by the operator',listeners:{sim:{alive:false,state:null,reason:null,age_ms:null},fc:{alive:false,state:null,reason:null,age_ms:null},...l}});
test('send state: absent or off is NOT SENT; "sending" is only the live sender\'s own report',()=>{
  assert.equal(transmitView(veh()).banner,'NOT SENT');assert.equal(transmitView(veh({transmit:tx('off')})).sending,false);assert.equal(transmitView(veh({transmit:tx('off')})).banner,'NOT SENT');
  const on=transmitView(veh({transmit:tx('sim',{sim:{alive:true,state:'sending',reason:'',age_ms:40}})}));
  assert.equal(on.sending,true);assert.equal(on.real,false);assert.match(on.banner,/^SENDING TO PX4 ON THE PC \(SIMULATION, OPEN LOOP/);assert.deepEqual(on.available,{sim:true,fc:false});
  assert.match(transmitView(veh({transmit:tx('sim',{sim:{alive:true,state:'holding',reason:'x',age_ms:40}})})).banner,/^NOT SENDING · PX4 on the PC .* is holding \(x\)/);
  const fc=transmitView(veh({transmit:tx('fc',{fc:{alive:true,state:'sending',streaming:true,reason:'',age_ms:40}})}));assert.equal(fc.real,true);assert.match(fc.banner,/FLIGHT CONTROLLER/);
  for(const l of [{fc:{alive:true,state:'holding',streaming:true,reason:'vehicle not in offboard',age_ms:40}},{fc:{alive:true,state:'refused',streaming:false,reason:'',age_ms:40}},{fc:{alive:true,state:'ready',streaming:null,reason:'',age_ms:40}},{fc:{alive:true,state:'stale',streaming:null,reason:'',age_ms:40}},{fc:{alive:false,state:'sending',streaming:true,reason:'',age_ms:40}},{}]){
    const v=transmitView(veh({transmit:tx('fc',l)}));assert.equal(v.sending,false);assert.equal(v.real,false);assert.match(v.banner,/^FOLLOW COMMAND NOT SENT/);}
  assert.equal(transmitView(veh({transmit:tx('everywhere')})).request,'off');});
test('send state: without fresh contact the page claims nothing; a dropped request says why',()=>{
  const live=veh({transmit:tx('fc',{fc:{alive:true,state:'sending',reason:'',age_ms:40}})});
  const stale=transmitView(live,false);assert.equal(stale.sending,false);assert.equal(stale.real,false);assert.match(stale.banner,/UNKNOWN/);
  assert.match(onboardView(live,false,80).detail,/UNKNOWN/);assert.equal(onboardView(live,true,80).send.sending,true);assert.match(onboardView(live,true,80).detail,/SENDING TO THE FLIGHT CONTROLLER/);
  const off=tx('off');off.reason='the flight-controller bridge stopped answering';assert.match(transmitView(veh({transmit:off})).status,/Sending is off: the flight-controller bridge stopped answering\./);
  assert.equal(transmitView(veh({transmit:tx('off')})).status==='',false);off.reason='not requested';assert.equal(transmitView(veh({transmit:off})).status,'');});
test('on-vehicle view: the person marker uses the picture offset of a valid command only',()=>{
  assert.equal(onboardView(veh({}, {offset:.4}),true,80).offset,.4);assert.equal(onboardView(veh({}, {offset:7}),true,80).offset,1);
  assert.equal(onboardView(veh(),true,80).offset,null);assert.equal(onboardView(veh({}, {offset:.4,valid:false}),true,80).offset,null);});
test('send state: the bridge\'s stream is shown from its own explicit statement, whatever the request',()=>{
  const fcl=(state,streaming,reason='')=>({fc:{alive:true,state,streaming,reason,age_ms:40}});
  const hold=transmitView(veh({transmit:tx('fc_hold',fcl('holding',true,'SIM: zero stream only'))}));
  assert.equal(hold.request,'fc_hold');assert.equal(hold.sending,false);assert.equal(hold.real,false);assert.equal(hold.stream,true);assert.match(hold.banner,/^FOLLOW COMMAND NOT SENT · ZERO STREAM CONTINUES .*\(SIM: zero stream only\)/);
  // astra-0121: a session that ended must not read as a zero stream, even if the state name says holding
  const ended=transmitView(veh({transmit:tx('fc_hold',fcl('holding',false,'no heartbeat; stream stopped'))}));assert.equal(ended.stream,false);assert.match(ended.banner,/the bridge is sending nothing \(no heartbeat; stream stopped\)/);assert.doesNotMatch(ended.banner,/ZERO STREAM CONTINUES/);
  // and Nowhere must not hide zeros that are still flowing
  const off=transmitView(veh({transmit:tx('off',fcl('stale',true,'SIM: zero stream only'))}));assert.equal(off.request,'off');assert.equal(off.stream,true);assert.match(off.banner,/^FOLLOW COMMAND NOT SENT · ZERO STREAM CONTINUES/);assert.match(off.status,/ZERO STREAM CONTINUES/);
  assert.match(transmitView(veh({transmit:tx('off',fcl('ready',false))})).banner,/the bridge is sending nothing/);
  for(const unknown of [null,undefined,'yes',1]){const v=transmitView(veh({transmit:tx('fc_hold',fcl('holding',unknown))}));assert.equal(v.stream,null);assert.match(v.banner,/stream state is unknown/);}
  const lie=transmitView(veh({transmit:tx('fc_hold',fcl('sending',true))}));assert.equal(lie.sending,false);assert.equal(lie.real,false);
  assert.equal(transmitView(veh({transmit:tx('fc',fcl('sending',true))})).real,true);
  assert.equal(transmitView(veh({transmit:tx('off',{fc:{alive:false,state:null,streaming:true,reason:null,age_ms:null}})})).stream,null);});
