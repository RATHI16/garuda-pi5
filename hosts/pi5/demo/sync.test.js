// Page-to-camera target synchronisation (astra-0058 finding 3): the bookkeeping class and the
// actual request function from app.js, run with a fake fetch.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {TargetSync}=require('./controller');
const state=(lock,ids=[7,8],over={})=>({stats:{instance:'proc'},frame:{epoch:'cam:0',seq:1},lock:{id:lock},
  tracks:ids.map(id=>({id,label:'person',coasting:false,x:100*id,y:50,w:80,h:300})),...over});

test('asserts the display target once, and a confirmation plus the live lock is agreement',()=>{
  const y=new TargetSync(),r=y.next(state(null),7,0);
  assert.deepEqual([r.id,r.instance,r.epoch,r.track.id],[7,'proc','cam:0',7]);
  assert.equal(y.next(state(null),7,10),null);                     // one request at a time
  assert.equal(y.status(state(null),7,10),'pending');
  assert.equal(y.done(r.token,true,20),true);
  assert.equal(y.status(state(null),7,30),'pending');              // shown state may predate the confirmation
  assert.equal(y.status(state(7),7,30),'synced');
  assert.equal(y.next(state(7),7,5000),null);
});
test('another display moving the camera lock is reported, not silently treated as agreement, and not fought',()=>{
  const y=new TargetSync(),r=y.next(state(null),7,0);y.done(r.token,true,10);
  assert.equal(y.status(state(7),7,100),'synced');
  const taken=state(8);                                            // someone else selected person 8
  assert.equal(y.status(taken,7,2000),'displaced');
  for(let t=2000;t<6000;t+=50)assert.equal(y.next(taken,7,t),null); // no tug of war
  y.forget();                                                      // operator selects here again
  const again=y.next(taken,7,6000);assert.equal(again.id,7);
});
test('re-acquiring the person under a new track ID asserts the new ID',()=>{
  const y=new TargetSync(),r=y.next(state(null),7,0);y.done(r.token,true,10);
  const back=state(7,[18]);                                        // camera still holds the old ID
  const n=y.next(back,18,1000);assert.equal(n.id,18);assert.notEqual(n.token,r.token);
});
test('detector restart or a new camera epoch invalidates an old confirmation',()=>{
  const y=new TargetSync(),r=y.next(state(null),7,0);y.done(r.token,true,10);
  assert.equal(y.status(state(7,[7,8],{stats:{instance:'proc2'}}),7,500),'unsynced');
  assert.equal(y.next(state(7,[7,8],{stats:{instance:'proc2'}}),7,500).instance,'proc2');
  const z=new TargetSync(),q=z.next(state(null),7,0);z.done(q.token,true,10);
  assert.equal(z.next(state(7,[7,8],{frame:{epoch:'cam:1',seq:1}}),7,500).epoch,'cam:1');
});
test('a delayed reply to a superseded or abandoned request confirms nothing',()=>{
  const y=new TargetSync(2000),first=y.next(state(null),7,0);
  assert.equal(y.next(state(null),7,1999),null);
  const second=y.next(state(null),7,2000);                         // the first never settled
  assert.equal(y.timeouts,1);assert.notEqual(second.token,first.token);
  assert.equal(y.done(first.token,true,2100),false);               // late reply to the abandoned one
  assert.equal(y.status(state(7),7,2100),'pending');
  assert.equal(y.done(second.token,true,2200),true);assert.equal(y.status(state(7),7,2300),'synced');
});
test('a refusal is retried, spaced out, and never counts as agreement',()=>{
  const y=new TargetSync(2000,600,150),r=y.next(state(null),7,0);
  assert.equal(y.done(r.token,false,10),false);assert.equal(y.status(state(7),7,20),'unsynced');
  assert.equal(y.next(state(null),7,100),null);
  assert.equal(y.next(state(null),7,150).id,7);assert.equal(y.failures,1);
});
test('nothing is requested without a visible person track, a target, or a detector identity',()=>{
  const y=new TargetSync();
  assert.equal(y.next(state(null),null,0),null);assert.equal(y.status(state(null),null,0),'idle');
  assert.equal(y.next(state(null,[8]),7,0),null);
  const coasting=state(null);coasting.tracks[0].coasting=true;assert.equal(y.next(coasting,7,0),null);
  const chair=state(null);chair.tracks[0].label='chair';assert.equal(y.next(chair,7,0),null);
  assert.equal(y.next(state(null,[7],{stats:{}}),7,0),null);
  assert.equal(y.next(null,7,0),null);
});

// The request function exactly as app.js defines it.
function page(fetchImpl){
  const src=fs.readFileSync(path.join(__dirname,'app.js'),'utf8'),a=src.indexOf('const targetSync='),b=src.indexOf('function clearCalibration()');
  assert.ok(a>0&&b>a);
  const ctx={Garuda:{TargetSync},fetch:fetchImpl,performance:{now:()=>ctx.now},AbortSignal:{timeout:ms=>({ms})},encodeURIComponent,now:0,out:{}};
  vm.runInNewContext(src.slice(a,b)+';out.targetSync=targetSync;out.send=sendTargetSync;',ctx);
  return {ctx,sync:ctx.out.targetSync,send:ctx.out.send};
}
const reply=(status,body={ok:true})=>Promise.resolve({status,ok:status>=200&&status<300,json:()=>body instanceof Error?Promise.reject(body):Promise.resolve(body)});
const settle=()=>new Promise(r=>setImmediate(r));
test('page request: exact-ID confirmation, refusal, and bounded requests',async()=>{
  const calls=[];let p=page((url,opt)=>{calls.push([url,opt]);return reply(200);});
  let r=p.sync.next(state(null),7,0);p.send(r);await settle();
  assert.equal(calls.length,1);assert.match(calls[0][0],/^\/api\/select_track\?id=7&epoch=cam%3A0&instance=proc$/);
  assert.equal(calls[0][1].method,'POST');assert.equal(calls[0][1].signal.ms,1500);
  assert.equal(p.sync.status(state(7),7,0),'synced');
  p=page(()=>reply(409));r=p.sync.next(state(null),7,0);p.send(r);await settle();
  assert.equal(p.sync.status(state(7),7,0),'unsynced');assert.equal(p.sync.failures,1);
});
test('page request: the position click is used only on 404 and only its success counts',async()=>{
  const urls=[];let p=page(url=>{urls.push(url);return reply(url.startsWith('/api/select_track')?404:200);});
  let r=p.sync.next(state(null),7,0);p.send(r);await settle();await settle();
  assert.equal(urls.length,2);assert.match(urls[1],/^\/api\/mono_click\?x=740&y=200$/);
  assert.equal(p.sync.status(state(7),7,0),'synced');
  p=page(url=>reply(url.startsWith('/api/select_track')?404:500));r=p.sync.next(state(null),7,0);p.send(r);await settle();await settle();
  assert.equal(p.sync.status(state(7),7,0),'unsynced');
});
test('page request: the position click answering 200 with ok:false, or an unreadable body, is a failed attempt',async()=>{
  // astra-0059 finding 1: the older route returns HTTP 200 {"ok":false,"class":null} when it picked nothing.
  for(const body of [{ok:false,class:null},{},null,{ok:'true'},new SyntaxError('not json')]){
    let clicks=0;const p=page(url=>url.startsWith('/api/select_track')?reply(404):(clicks++,reply(200,body)));
    const r=p.sync.next(state(null),7,0);p.send(r);await settle();await settle();await settle();
    assert.equal(clicks,1);assert.equal(p.sync.acked,null);assert.equal(p.sync.failures,1);
    assert.equal(p.sync.status(state(8),7,5000),'unsynced');        // not 'displaced': nothing was ever confirmed
    assert.ok(p.sync.next(state(null),7,200));                      // and it tries again
  }
});
test('page request: a fetch that never settles does not block synchronisation for ever',async()=>{
  let n=0;const p=page(()=>{n++;return n===1?new Promise(()=>{}):reply(200);});
  const first=p.sync.next(state(null),7,0);p.send(first);await settle();
  assert.equal(p.sync.next(state(null),7,1000),null);
  const second=p.sync.next(state(null),7,2000);assert.ok(second);p.ctx.now=2000;p.send(second);await settle();
  assert.equal(p.sync.status(state(7),7,2000),'synced');
  await assert.rejects(Promise.reject(new Error('x')));            // keeps the runner honest about async use
});
test('page request: a network error is a failed attempt, not agreement',async()=>{
  const p=page(()=>Promise.reject(new Error('offline')));const r=p.sync.next(state(null),7,0);p.send(r);await settle();
  assert.equal(p.sync.status(state(7),7,0),'unsynced');assert.equal(p.sync.failures,1);
});
// ---- PX4 lab link ----------------------------------------------------------------------
const {LabLink,labView,Unlinker,labSourceFor,capabilities}=require('./controller');
const labState=(over={})=>({phase:'GROUND',armed:false,telemetry_fresh:true,source_kind:'pi',accept_preview_stereo_in_simulation:true,altitude:0,reason:'Select your person ID',error:'',...over});
// Camera states as the two kinds of camera publish them.
const oakCam=(lock=7,ids=[7,8])=>{const c=state(lock,ids);c.frame.source='oak-rgb';c.stereo={status:'ok',z_m:2};return c;};
const monoCam=(lock=7,ids=[7,8])=>{const c=state(lock,ids);c.frame.source='csi';c.stats.capabilities={distance_source:'mono_estimate'};return c;};
test('lab link: switches the lab to the stereo source only on the ground, and not repeatedly',()=>{
  const l=new LabLink(2000),c=oakCam();
  assert.deepEqual(l.sync(labState(),0,null,c),[{command:'source',kind:'fixed_stereo'}]);
  assert.deepEqual(l.sync(labState(),500,null,c),[]);assert.deepEqual(l.sync(labState(),2000,null,c),[{command:'source',kind:'fixed_stereo'}]);
  assert.deepEqual(new LabLink().sync(labState({source_kind:'fixed_stereo'}),0,null,c),[]);
  assert.deepEqual(new LabLink().sync(labState({phase:'FLYING',armed:true}),0,null,c),[]);       // never while flying
  assert.deepEqual(new LabLink().sync(labState({armed:undefined}),0,null,c),[]);assert.deepEqual(new LabLink().sync(null,0,null,c),[]);
});
test('lab link: a one-lens camera asks for the estimate source, never the stereo one, and an unknown camera asks for nothing',()=>{
  assert.deepEqual(new LabLink().sync(labState(),0,null,monoCam()),[{command:'source',kind:'mono_estimate'}]);
  assert.deepEqual(new LabLink().sync(labState({source_kind:'fixed_stereo'}),0,null,monoCam()),[{command:'source',kind:'mono_estimate'}]);
  assert.deepEqual(new LabLink().sync(labState({source_kind:'mono_estimate'}),0,null,oakCam()),[{command:'source',kind:'fixed_stereo'}]);
  assert.deepEqual(new LabLink().sync(labState(),0,7,null),[]);assert.deepEqual(new LabLink().sync(labState(),0,7,state(7)),[]);
  // The OAK without its stereo block is not driven by an estimate.
  const colourOnly=state(7);colourOnly.frame.source='oak-rgb';colourOnly.stats.capabilities={distance_source:'mono_estimate'};
  assert.deepEqual(new LabLink().sync(labState(),0,7,colourOnly),[]);
  // Selection is passed on for the estimate source the same way.
  assert.deepEqual(new LabLink().sync(labState({source_kind:'mono_estimate',target_id:null}),0,7,monoCam()),[{command:'select',id:7,epoch:'cam:0'}]);
  assert.equal(new LabLink().selection(labState({source_kind:'fixed_stereo',target_id:null}),7,monoCam()),null);
});
test('lab link: take-off follows only when a person is selected here; settings map radius to limit',()=>{
  const l=new LabLink();assert.deepEqual(l.takeoff(7),{command:'takeoff_follow'});assert.deepEqual(l.takeoff(null),{command:'takeoff'});
  assert.deepEqual(l.settings({gap:3,radius:8,speed:1.5}),{command:'settings',gap:3,speed:1.5,limit:8});
});
// The real lab.js in a stubbed page: what it sends, to whom, and when.
function labPage(labReply,store={}){
  const src=fs.readFileSync(path.join(__dirname,'lab.js'),'utf8'),calls=[],els={},page={takeoff:0,land:0,pause:0,follow:0,vehicleChanged:0,selected:null};
  const el=id=>els[id]||(els[id]={id,onclick:null,oninput:null,append(){},after(){},set textContent(v){this.text=v;},get textContent(){return this.text;}});
  let timers=[];
  const ctx={Garuda:{LabLink,labView,Unlinker,labSourceFor,capabilities,mountOf:require('./controller').mountOf},F:{id:7,settings:{gap:2,radius:6,speed:1}},S:oakCam(),V:null,$:el,performance:{now:()=>ctx.now},now:0,
    vehicleChanged:()=>{page.vehicleChanged++;},URL,
    document:{createElement:()=>el('new'+Object.keys(els).length),querySelector:()=>el('flightCommands')},
    localStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=v;}},
    AbortSignal:{timeout:()=>({})},setTimeout:f=>{timers.push(f);},setInterval:()=>1,clearInterval:()=>{},window:{select:id=>{page.selected=id;}},
    fetch:(url,opt)=>{const body=opt&&opt.body?JSON.parse(opt.body):null;calls.push({url:url.replace('http://127.0.0.1:8099',''),body,session:opt?.headers?.['X-Garuda-Session']});
      const r=labReply(url,body,calls.length);return r instanceof Error?Promise.reject(r):Promise.resolve(r);}};
  for(const [id,key]of[['takeoff','takeoff'],['land','land'],['pause','pause'],['followTarget','follow']])el(id).onclick=()=>{page[key]++;};
  vm.createContext(ctx);vm.runInContext('let V=null;'+src.replace("'use strict';",''),ctx);
  const poll=async()=>{const f=timers;timers=[];for(const t of f)await t();await settle();};
  const commands=()=>calls.filter(c=>c.url==='/api/command').map(c=>c.body.command);
  return {ctx,els,page,calls,commands,poll,store,view:()=>{ctx.window.labFrame();return vm.runInContext('V',ctx);},linkButton:()=>Object.values(els).find(e=>typeof e.text==='string'&&/PX4 lab|Unlinking/.test(e.text)),status:()=>Object.values(els).find(e=>'hidden' in e)};
}
const labReply=(lab,over={})=>(url,body)=>url.endsWith('/api/state')?{ok:true,json:()=>Promise.resolve(typeof lab==='function'?lab():lab)}:{ok:true,status:202,json:()=>Promise.resolve({queued:true,id:'c1',...over})};
test('lab page: not linked, every button does only what the page always did and nothing is sent',async()=>{
  const p=labPage(labReply(labState({source_kind:'fixed_stereo'})));await settle();
  p.els.takeoff.onclick();p.els.land.onclick();p.els.pause.onclick();p.els.followTarget.onclick();p.ctx.window.select(7);await p.poll();
  assert.deepEqual([p.page.takeoff,p.page.land,p.page.pause,p.page.follow,p.page.selected],[1,1,1,1,7]);
  assert.deepEqual(p.calls,[]);assert.equal(p.view(),null);
});
test('lab page: linked, the buttons command the lab ONLY, with this tab\'s session, and the page vehicle is never driven',async()=>{
  const p=labPage(labReply(labState({source_kind:'fixed_stereo',target_id:7})));await settle();
  p.linkButton().onclick();assert.equal(p.store['garuda-lab-link'],'1');assert.equal(p.page.vehicleChanged,1);
  await p.poll();assert.equal(p.view().contact,true);
  p.calls.length=0;p.els.takeoff.onclick();await settle();p.els.pause.onclick();p.els.land.onclick();await settle();
  assert.deepEqual(p.commands(),['takeoff','hold','land']);
  assert.deepEqual([p.page.takeoff,p.page.land,p.page.pause],[0,0,0]);
  const sessions=new Set(p.calls.map(c=>c.session));assert.equal(sessions.size,1);assert.match([...sessions][0],/^\S{8,128}$/);
  // The session is never stored anywhere a reload or a duplicated tab could pick it up.
  assert.deepEqual(Object.keys(p.store).sort(),['garuda-lab-contact','garuda-lab-link']);assert.equal(JSON.stringify(p.store).includes([...sessions][0]),false);
});
test('lab page: linked without contact, movement is not sent and nothing falls back to the page vehicle; land and hold are still tried',async()=>{
  const p=labPage(()=>new Error('offline'),{'garuda-lab-link':'1'});await settle();await p.poll();
  assert.equal(p.view().contact,false);
  p.els.takeoff.onclick();p.els.followTarget.onclick();await settle();
  assert.deepEqual(p.commands(),[]);assert.deepEqual([p.page.takeoff,p.page.follow],[0,0]);
  p.els.land.onclick();p.els.pause.onclick();await settle();
  assert.deepEqual(p.commands(),['land','hold']);assert.deepEqual([p.page.land,p.page.pause],[0,0]);
});
test('lab page: unlinking in the air sends hold and stays linked until the lab reports that hold applied',async()=>{
  let lab=labState({source_kind:'fixed_stereo',target_id:7,phase:'FLYING',armed:true,behavior:'follow',control:{owner:null,expired:false,last_command:null}});
  const p=labPage(labReply(()=>lab,{id:'h1'}),{'garuda-lab-link':'1'});await settle();p.ctx.now=100;await p.poll();
  p.calls.length=0;p.ctx.now=1000;p.linkButton().onclick();await settle();
  assert.deepEqual(p.commands(),['hold']);assert.equal(p.store['garuda-lab-link'],'1');assert.match(p.linkButton().text,/waiting for hold/);
  p.ctx.now=1300;await p.poll();assert.equal(p.store['garuda-lab-link'],'1');                  // still following: still linked
  lab={...lab,behavior:'hold',control:{owner:null,expired:false,last_command:{id:'h1',name:'hold',applied:true,error:null}}};
  p.ctx.now=1600;await p.poll();
  assert.equal(p.store['garuda-lab-link'],'0');assert.equal(p.view(),null);assert.equal(p.linkButton().text,'Link PX4 lab');
});
test('lab page: another display in control gets no unasked commands from this page',async()=>{
  const lab=labState({source_kind:'pi',control:{owner:'someone-else',expired:false,last_command:null}});
  const p=labPage(labReply(lab),{'garuda-lab-link':'1'});await settle();await p.poll();await p.poll();
  assert.deepEqual(p.commands(),[]);assert.equal(p.view().control,'other');
});
test('lab page: a link restored by a reload only watches; control is taken by an operator action, never by itself',async()=>{
  const free=labState({source_kind:'pi',control:{owner:null,expired:false,last_command:null}});
  // Reloaded with the link remembered: nothing is sent, however long the page watches.
  const q=labPage(labReply(free),{'garuda-lab-link':'1'});await settle();await q.poll();q.ctx.now=5000;await q.poll();
  assert.deepEqual(q.commands(),[]);assert.equal(q.calls.some(c=>c.url==='/api/heartbeat'),false);
  // Linking by hand in this page load is such an action: the lab is put on this camera's source.
  const r=labPage(labReply(free));await settle();r.linkButton().onclick();await r.poll();
  assert.deepEqual(r.commands(),['source']);
  // Explicitly reconnecting can choose the source after a lapsed lease, once; reload alone cannot.
  const lapsed=labState({source_kind:'pi',control:{owner:null,expired:true,last_command:null}});
  const x=labPage(labReply(lapsed));await settle();x.linkButton().onclick();await x.poll();await x.poll();
  assert.deepEqual(x.commands(),['source']);
  // Take off on a lab that is on another source switches the source and stops there.
  const t=labPage(labReply(free),{'garuda-lab-link':'1'});await settle();await t.poll();t.els.takeoff.onclick();await settle();
  assert.deepEqual(t.commands(),['source']);
});
test('display adopts the camera identity decision only for its own target and a visible person',()=>{
  const {adoptIdentity}=require('./controller');
  const idn=(over={})=>({target:18,rebound_from:7,confirmed:true,frame_seq:1,frame_epoch:'cam:0',instance:'proc',...over});
  const s=state(18,[18],{stats:{instance:'proc',identity:idn()}});
  assert.equal(adoptIdentity(7,s),18);assert.equal(adoptIdentity(9,s),9);assert.equal(adoptIdentity(null,s),null);
  assert.equal(adoptIdentity(7,state(18,[5],{stats:{instance:'proc',identity:idn()}})),7);       // new ID not visible here
  for(const bad of [{confirmed:false},{frame_seq:9},{frame_epoch:'cam:1'},{instance:'other'}])assert.equal(adoptIdentity(7,state(18,[18],{stats:{instance:'proc',identity:idn(bad)}})),7);
  assert.equal(adoptIdentity(7,state(7,[7],{stats:{identity:{target:7,rebound_from:null}}})),7);
  assert.equal(adoptIdentity(7,state(7,[7])),7);
});
test('lab link: a selection made before linking is passed on, on the ground only, and Take off can send it first',()=>{
  const l=new LabLink(2000),lab=labState({source_kind:'fixed_stereo',target_id:null});
  assert.deepEqual(l.sync(lab,0,7,oakCam()),[{command:'select',id:7,epoch:'cam:0'}]);
  assert.deepEqual(l.sync(lab,500,7,oakCam()),[]);                                            // not hammered
  assert.deepEqual(new LabLink().sync(labState({source_kind:'fixed_stereo',target_id:7}),0,7,oakCam()),[]);        // already agreed
  assert.deepEqual(new LabLink().sync(labState({source_kind:'fixed_stereo',target_id:7,requires_reselection:true}),0,7,oakCam()),[{command:'select',id:7,epoch:'cam:0'}]);
  const flying=labState({source_kind:'fixed_stereo',target_id:null,phase:'FLYING',armed:true});
  assert.deepEqual(new LabLink().sync(flying,0,7,oakCam()),[]);                               // nothing selected unasked in the air
  assert.deepEqual(new LabLink().selection(flying,7,oakCam()),{command:'select',id:7,epoch:'cam:0'});   // but Take off may send it
  assert.equal(new LabLink().selection(lab,7,oakCam(8,[8])),null);                             // person not visible now
  assert.equal(new LabLink().selection(lab,null,oakCam()),null);
  assert.deepEqual(new LabLink().sync(labState(),0,7,oakCam()),[{command:'source',kind:'fixed_stereo'}]);          // source first
});

test('lab page: a reload while this page owned the lab comes back as an observer with a new session',async()=>{
  // First page load: link and take off, which makes this page the owner.
  const first=labPage(labReply(labState({source_kind:'fixed_stereo',target_id:7})));await settle();first.linkButton().onclick();await first.poll();
  first.els.takeoff.onclick();await settle();const owner=first.calls.find(c=>c.url==='/api/command').session;
  // Reload within the lease: same storage, lab still names the first page's session as owner.
  const flying=labState({source_kind:'pi',phase:'FLYING',armed:true,behavior:'follow',target_id:7,control:{owner,lease_ms:2500,expired:false,last_command:null}});
  const again=labPage(labReply(flying),first.store);await settle();await again.poll();again.ctx.now=900;await again.poll();
  assert.equal(again.view().control,'other');assert.deepEqual(again.commands(),[]);
  assert.equal(again.calls.some(c=>c.url==='/api/heartbeat'),false);
  // What it does send later carries a different session.
  again.els.land.onclick();await settle();const s=again.calls.find(c=>c.url==='/api/command').session;
  assert.notEqual(s,owner);assert.match(s,/^\S{8,128}$/);
});
test('lab page: unlink is refused while the lab is out of contact after it was last seen flying, and free if it never answered',async()=>{
  let up=true;const flying=labState({source_kind:'fixed_stereo',phase:'FLYING',armed:true,behavior:'follow',target_id:7});
  const p=labPage((url,body,n)=>up?labReply(flying)(url,body):new Error('offline'),{'garuda-lab-link':'1'});await settle();await p.poll();
  up=false;p.ctx.now=5000;await p.poll();assert.equal(p.view().contact,false);
  p.linkButton().onclick();p.ctx.now=6000;p.linkButton().onclick();await settle();
  assert.equal(p.store['garuda-lab-link'],'1');assert.equal(p.linkButton().text,'PX4 lab: linked');
  const never=labPage(()=>new Error('offline'),{'garuda-lab-link':'1'});await settle();await never.poll();
  never.linkButton().onclick();assert.equal(never.store['garuda-lab-link'],'0');assert.equal(never.view(),null);
});

test('lab page: unlink always works; without the lab it says nothing was confirmed, also across a reload',async()=>{
  // Seen landed and disarmed, nothing sent; then the lab stops answering (owner, 2026-11-16: the link must never be stuck).
  let up=true;const ground=labState({source_kind:'fixed_stereo',target_id:7});
  const p=labPage((url,body)=>up?labReply(ground)(url,body):new Error('offline'),{'garuda-lab-link':'1'});await settle();await p.poll();
  assert.equal(p.store['garuda-lab-contact'],'1');
  up=false;p.ctx.now=5000;await p.poll();p.linkButton().onclick();
  assert.equal(p.store['garuda-lab-link'],'0');assert.match(p.status().text,/Unlinked WITHOUT the PX4 lab's confirmation \(no contact with the lab\).*state is unknown/);
  // Reload while still linked and the lab never answers in the new page: same.
  const again=labPage(()=>new Error('offline'),{'garuda-lab-link':'1','garuda-lab-contact':'1'});await settle();await again.poll();again.linkButton().onclick();
  assert.equal(again.store['garuda-lab-link'],'0');assert.match(again.status().text,/WITHOUT/);
  // With the lab back, landed and disarmed, unlinking is confirmed and carries no warning.
  const back=labPage(labReply(ground),{'garuda-lab-link':'1','garuda-lab-contact':'1'});await settle();await back.poll();back.linkButton().onclick();
  assert.equal(back.store['garuda-lab-link'],'0');assert.equal(back.store['garuda-lab-contact'],'0');assert.doesNotMatch(back.status().text||'',/WITHOUT/);
});
test('lab page: a command sent on a link the lab never answered on counts as contact, so the unlink carries the warning',async()=>{
  const p=labPage(()=>new Error('offline'),{'garuda-lab-link':'1'});await settle();await p.poll();
  p.els.land.onclick();await settle();p.linkButton().onclick();
  assert.equal(p.store['garuda-lab-link'],'0');assert.match(p.status().text,/Unlinked WITHOUT the PX4 lab's confirmation/);
});
test('lab page: ground, Take off, state lost: unlink refused; and GROUND right after Take off is not taken for landed',async()=>{
  let up=true,lab=labState({source_kind:'fixed_stereo',target_id:7,control:{owner:null,expired:false,last_command:null}});
  const p=labPage((url,body)=>up?labReply(()=>lab,{id:'t1'})(url,body):new Error('offline'),{'garuda-lab-link':'1'});await settle();await p.poll();
  p.ctx.now=1000;p.els.takeoff.onclick();await settle();assert.deepEqual(p.commands(),['takeoff']);
  // Contact still up and the state still says GROUND (the take-off is only queued): no free unlink.
  p.ctx.now=1100;await p.poll();p.calls.length=0;p.linkButton().onclick();await settle();
  assert.equal(p.store['garuda-lab-link'],'1');assert.deepEqual(p.commands(),['hold']);
  // Then the state is lost altogether: still refused, whatever the wait.
  up=false;p.ctx.now=9000;await p.poll();p.ctx.now=20000;await p.poll();p.linkButton().onclick();await settle();
  assert.equal(p.store['garuda-lab-link'],'1');
  // The lab reports that it refused that take-off: the grounded state now counts.
  const q=labPage(labReply(()=>lab,{id:'t1'}),{'garuda-lab-link':'1'});await settle();await q.poll();
  q.ctx.now=1000;q.els.takeoff.onclick();await settle();
  lab={...lab,control:{owner:null,expired:false,last_command:{id:'t1',name:'takeoff_follow',applied:false,error:'PX4 telemetry unavailable'}}};
  q.ctx.now=1200;await q.poll();q.linkButton().onclick();await settle();
  assert.equal(q.store['garuda-lab-link'],'0');
});
test('lab page: Reset settings also reaches the lab when linked',async()=>{
  const p=labPage(labReply(labState({source_kind:'fixed_stereo',target_id:7})),{'garuda-lab-link':'1'});await settle();await p.poll();
  let pageReset=0;p.calls.length=0;
  // (the stub page's own handler was installed before lab.js wrapped it)
  p.els.tuningReset.onclick();await settle();
  assert.deepEqual(p.calls.filter(c=>c.url==='/api/command').map(c=>c.body),[{command:'settings',gap:2,speed:1,limit:6}]);
});

test('lab page: a later command of this page is a valid barrier when the take-off\'s own acknowledgement was overwritten',async()=>{
  let n=0,lab=labState({source_kind:'fixed_stereo',target_id:7,control:{owner:null,expired:false,last_command:null}});
  const ids=(url,body)=>url.endsWith('/api/state')?{ok:true,json:()=>Promise.resolve(lab)}:{ok:true,status:202,json:()=>Promise.resolve({queued:true,id:'c'+(++n)})};
  const p=labPage(ids,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.els.takeoff.onclick();await settle();                       // c1 = take-off (refused by the lab, as it turns out)
  p.els.gapSetting.oninput();await settle();                    // c2 = settings, sent after the take-off request finished
  // Both were executed in one controller tick: only c2 is ever visible as the last command.
  lab={...lab,control:{owner:null,expired:false,last_command:{id:'c2',name:'settings',applied:true,error:null}}};
  p.ctx.now=500;await p.poll();p.calls.length=0;p.linkButton().onclick();await settle();
  assert.equal(p.store['garuda-lab-link'],'0');assert.deepEqual(p.commands(),[]);
  // A command of some OTHER page as the last command is no barrier: still treated as possibly airborne.
  n=0;lab=labState({source_kind:'fixed_stereo',target_id:7,control:{owner:null,expired:false,last_command:null}});
  const q=labPage(ids,{'garuda-lab-link':'1'});await settle();await q.poll();q.els.takeoff.onclick();await settle();
  lab={...lab,control:{owner:null,expired:false,last_command:{id:'someone-elses',name:'settings',applied:true,error:null}}};
  q.ctx.now=500;await q.poll();q.calls.length=0;q.linkButton().onclick();await settle();
  assert.equal(q.store['garuda-lab-link'],'1');assert.deepEqual(q.commands(),['hold']);
  // ... until this page's own hold (c2 here) is reported applied on a landed, disarmed vehicle.
  lab={...lab,control:{owner:null,expired:false,last_command:{id:'c2',name:'hold',applied:true,error:null}}};
  q.ctx.now=900;await q.poll();assert.equal(q.store['garuda-lab-link'],'0');
});

// Follow/Circle must not reach the lab before this page's selection has been accepted.
function deferredSelect(lab){
  let release=null;const held=[];
  const reply=(url,body)=>{
    if(url.endsWith('/api/state'))return {ok:true,json:()=>Promise.resolve(lab())};
    if(body.command==='select')return new Promise(res=>{release=(ok=true)=>res(ok?{ok:true,status:202,json:()=>Promise.resolve({queued:true,id:'s1'})}:{ok:false,status:409,json:()=>Promise.resolve({error:'camera refused'})});});
    held.push(body.command);return {ok:true,status:202,json:()=>Promise.resolve({queued:true,id:'m'+held.length})};
  };
  return {reply,release:ok=>release(ok),held};
}
const flyingOn=(target)=>()=>labState({source_kind:'fixed_stereo',phase:'FLYING',armed:true,behavior:'hold',target_id:target,lock_state:'LOCKED'});
test('lab page: Follow waits for this page\'s selection to be accepted, and never follows the lab\'s old target',async()=>{
  // The lab still holds person 8; this page has person 7.
  const d=deferredSelect(flyingOn(8)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.els.followTarget.onclick();await settle();await settle();
  assert.deepEqual(p.commands(),['select']);assert.deepEqual(d.held,[]);              // nothing moves while the selection is open
  d.release(true);await settle();await settle();
  assert.deepEqual(p.commands(),['select','follow']);
  assert.deepEqual(p.calls.filter(c=>c.url==='/api/command')[0].body,{command:'select',id:7,epoch:'cam:0'});
});
test('lab page: a refused selection sends no motion; a target changed while waiting cancels it',async()=>{
  const d=deferredSelect(flyingOn(8)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.els.circleTarget.onclick();await settle();d.release(false);await settle();await settle();
  assert.deepEqual(p.commands(),['select']);assert.match(p.status().text,/camera refused/);
  const e=deferredSelect(flyingOn(8)),q=labPage(e.reply,{'garuda-lab-link':'1'});await settle();await q.poll();
  q.els.followTarget.onclick();await settle();q.ctx.F.id=9;e.release(true);await settle();await settle();
  assert.deepEqual(q.commands(),['select']);assert.deepEqual(e.held,[]);
  // The camera restarting during the wait cancels it as well.
  const g=deferredSelect(flyingOn(8)),r=labPage(g.reply,{'garuda-lab-link':'1'});await settle();await r.poll();
  r.els.followTarget.onclick();await settle();r.ctx.S={...r.ctx.S,frame:{...r.ctx.S.frame,epoch:'cam:1'}};g.release(true);await settle();await settle();
  assert.deepEqual(g.held,[]);
});
test('lab page: Follow with no person on this page, or with a person the lab cannot be given, moves nothing',async()=>{
  const d=deferredSelect(flyingOn(8)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.ctx.F.id=null;p.els.followTarget.onclick();await settle();
  assert.deepEqual(p.commands(),[]);assert.match(p.status().text,/Select a person on this page first/);
  // Person 5 is selected here but is not in view now, so no selection can be sent; the lab is on 8.
  p.ctx.F.id=5;p.els.circleTarget.onclick();await settle();
  assert.deepEqual(p.commands(),[]);assert.match(p.status().text,/does not have this page's person/);
  // The lab already holds this page's person: follow goes straight out.
  const e=deferredSelect(flyingOn(7)),q=labPage(e.reply,{'garuda-lab-link':'1'});await settle();await q.poll();
  q.els.followTarget.onclick();await settle();assert.deepEqual(q.commands(),['follow']);
});
test('lab page: a click on a box followed at once by Follow keeps the order selection, then motion',async()=>{
  const d=deferredSelect(flyingOn(7)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.ctx.window.select(7);p.els.followTarget.onclick();await settle();await settle();
  assert.deepEqual(p.commands(),['select']);
  d.release(true);await settle();await settle();await settle();
  assert.deepEqual(p.commands(),['select','follow']);
});

test('lab page: Take off waits for the selection too, and is dropped if the person or camera run changed meanwhile',async()=>{
  const ground=target=>()=>labState({source_kind:'fixed_stereo',target_id:target});
  const d=deferredSelect(ground(8)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.els.takeoff.onclick();await settle();await settle();
  assert.deepEqual(p.commands(),['select']);d.release(true);await settle();await settle();
  assert.deepEqual(p.commands(),['select','takeoff']);
  const e=deferredSelect(ground(8)),q=labPage(e.reply,{'garuda-lab-link':'1'});await settle();await q.poll();
  q.els.takeoff.onclick();await settle();await settle();q.ctx.F.id=9;e.release(true);await settle();await settle();
  assert.deepEqual(q.commands(),['select']);assert.deepEqual(e.held,[]);
  const g=deferredSelect(ground(8)),r=labPage(g.reply,{'garuda-lab-link':'1'});await settle();await r.poll();
  r.els.takeoff.onclick();await settle();await settle();r.ctx.S={...r.ctx.S,frame:{...r.ctx.S.frame,epoch:'cam:1'}};g.release(true);await settle();await settle();
  assert.deepEqual(g.held,[]);
  const h=deferredSelect(ground(8)),t=labPage(h.reply,{'garuda-lab-link':'1'});await settle();await t.poll();
  t.els.takeoff.onclick();await settle();await settle();h.release(false);await settle();await settle();
  assert.deepEqual(h.held,[]);assert.match(t.status().text,/camera refused/);
});

test('lab page: a Follow or Take off that was waiting is never sent after a later Hold, Land or Release',async()=>{
  for(const [first,stopper,sent] of [['followTarget','pause','hold'],['circleTarget','land','land'],['followTarget','reset','release'],['takeoff','pause','hold']]){
    const lab=first==='takeoff'?()=>labState({source_kind:'fixed_stereo',target_id:8}):flyingOn(8);
    const d=deferredSelect(lab),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
    p.els[first].onclick();await settle();await settle();assert.deepEqual(p.commands(),['select'],first);
    p.els[stopper].onclick();await settle();
    d.release(true);await settle();await settle();await settle();
    assert.deepEqual(p.commands(),['select',sent],first+' then '+stopper);
    assert.deepEqual(d.held,[sent],first+' then '+stopper);
  }
});
test('lab page: a refused reselect blocks motion even when the lab already shows the same person id',async()=>{
  // The lab's target id equals this page's id, so no select would be needed; but the operator's
  // own click on the box was refused by the camera.
  const d=deferredSelect(flyingOn(7)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.ctx.window.select(7);p.els.followTarget.onclick();await settle();await settle();
  assert.deepEqual(p.commands(),['select']);
  d.release(false);await settle();await settle();await settle();
  assert.deepEqual(p.commands(),['select']);assert.deepEqual(d.held,[]);assert.match(p.status().text,/refused/);
  // It keeps blocking until a new click succeeds.
  p.els.circleTarget.onclick();await settle();await settle();assert.deepEqual(d.held,[]);
  p.ctx.window.select(7);await settle();d.release(true);await settle();await settle();
  p.els.followTarget.onclick();await settle();await settle();assert.deepEqual(d.held,['follow']);
});
test('lab page: a newer action replaces a waiting one, and unlinking then relinking does not revive it',async()=>{
  const d=deferredSelect(flyingOn(8)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.els.followTarget.onclick();await settle();await settle();
  p.els.circleTarget.onclick();await settle();await settle();             // waits on its own select (the stub holds only the latest)
  d.release(true);await settle();await settle();await settle();
  assert.equal(d.held.includes('follow'),false);
  // Detector process changed during the wait: dropped.
  const e=deferredSelect(flyingOn(8)),q=labPage(e.reply,{'garuda-lab-link':'1'});await settle();await q.poll();
  q.els.followTarget.onclick();await settle();await settle();q.ctx.S={...q.ctx.S,stats:{...q.ctx.S.stats,instance:'restarted'}};
  e.release(true);await settle();await settle();assert.deepEqual(e.held,[]);
  // Unlink (lab on the ground, so it drops at once) and link again while a take-off waits: not revived.
  const g=deferredSelect(()=>labState({source_kind:'fixed_stereo',target_id:8})),r=labPage(g.reply,{'garuda-lab-link':'1'});await settle();await r.poll();
  r.els.takeoff.onclick();await settle();await settle();assert.deepEqual(r.commands(),['select']);
  r.linkButton().onclick();await settle();
  if(r.store['garuda-lab-link']==='0'){r.linkButton().onclick();await r.poll();}
  g.release(true);await settle();await settle();await settle();
  assert.equal(g.held.some(c=>c.startsWith('takeoff')),false);
});

test('lab page: asking to unlink in the air cancels a Follow that was waiting, before the link is dropped',async()=>{
  const d=deferredSelect(flyingOn(8)),p=labPage(d.reply,{'garuda-lab-link':'1'});await settle();await p.poll();
  p.els.followTarget.onclick();await settle();await settle();assert.deepEqual(p.commands(),['select']);
  p.ctx.now=1000;p.linkButton().onclick();await settle();       // airborne: sends hold and stays linked, waiting
  assert.equal(p.store['garuda-lab-link'],'1');
  d.release(true);await settle();await settle();await settle();
  assert.deepEqual(p.commands(),['select','hold']);assert.deepEqual(d.held,['hold']);
});
test('lab page: on-drone placement keeps one link and switches source on the ground',async()=>{
 const p=labPage(labReply(labState({source_kind:'fixed_stereo',target_id:7})),{'garuda-lab-link':'1'});
 await settle();await p.poll();
 p.ctx.S.stats={...(p.ctx.S.stats||{}),capabilities:{camera:'oak',distance_source:'stereo',mount:'vehicle'}};
 p.linkButton().onclick(); // explicitly disconnect
 p.linkButton().onclick(); // explicit control in this page load
 await p.poll();
 assert.equal(p.store['garuda-lab-link'],'1');
 assert.ok(p.calls.some(c=>c.body?.command==='source'&&c.body.kind==='pi_onboard'));
});
test('lab page: stale unlink keeps its warning after failed hold HTTP',async()=>{
 const lab=labState({phase:'FLYING',armed:true,telemetry_fresh:false,behavior:'follow',source_kind:'fixed_stereo',target_id:7});
 const p=labPage((url,body)=>url.endsWith('/api/command')?new Error('offline'):labReply(lab)(url,body),{'garuda-lab-link':'1','garuda-lab-contact':'1'});
 await settle();await p.poll();p.linkButton().onclick();await settle();await settle();
 assert.equal(p.store['garuda-lab-link'],'0');assert.match(p.status().text,/Unlinked WITHOUT/);
});
test('lab page: onboard Takeoff and Follow use one controller; Hold stops Pi grant',async()=>{
 const lab=labState({source_kind:'pi_onboard',target_id:7,phase:'FLYING',armed:true,control:{owner:null,expired:false,last_command:null}});
 const p=labPage((url,body)=>url.startsWith('/api/transmit')?{ok:true,json:async()=>({ok:true})}:labReply(lab)(url,body),{'garuda-lab-link':'1'});
 p.ctx.S.stats={instance:'pi-1',capabilities:{camera:'oak',distance_source:'stereo',mount:'vehicle'}};
 await settle();await p.poll();p.els.followTarget.onclick();await settle();await settle();await settle();
 assert.ok(p.commands().includes('follow'));
 assert.deepEqual(p.calls.filter(c=>c.url.startsWith('/api/transmit')).map(c=>c.url),['/api/transmit?target=off','/api/transmit?target=sim']);
 p.els.pause.onclick();await settle();await settle();assert.equal(p.commands().at(-1),'hold');
 assert.equal(p.calls.filter(c=>c.url.startsWith('/api/transmit')).at(-1).url,'/api/transmit?target=off');
});

test('explicit Connect can switch an on-drone source after a previous lease expired; reload cannot',async()=>{
 const lab=labState({source_kind:'fixed_stereo',control:{owner:null,expired:true,last_command:null}});
 const p=labPage(labReply(lab));await settle();
 p.ctx.S.stats={instance:'pi-1',capabilities:{camera:'oak',distance_source:'stereo',mount:'vehicle'}};
 p.ctx.window.garudaLab.connect();await p.poll();
 assert.deepEqual(p.commands(),['source']);assert.equal(p.calls.find(c=>c.body?.command==='source').body.kind,'pi_onboard');
 await p.poll();assert.deepEqual(p.commands(),['source']);
 const q=labPage(labReply(lab),{'garuda-lab-link':'1'});
 q.ctx.S.stats={instance:'pi-1',capabilities:{camera:'oak',distance_source:'stereo',mount:'vehicle'}};
 await settle();await q.poll();assert.deepEqual(q.commands(),[]);
});

test('on-drone ready message uses the normal Follow control, not hidden transport switches',()=>{
 const v=labView(true,labState({source_kind:'pi_onboard',reason:'Sending to PC is off; choose Nowhere then PX4 on the PC again'}));
 assert.match(v.reason,/press Follow/);assert.doesNotMatch(v.reason,/Nowhere/);
});

test('test modes refuse airborne changes and reset without sending commands',async()=>{
 const p=labPage(labReply(labState({phase:'FLYING',armed:true,landed:false})),{'garuda-lab-link':'1'});
 await settle();await p.poll();p.calls.length=0;
 await assert.rejects(p.ctx.window.garudaLab.setTestMode('gazebo'),/Land and disarm/);
 assert.equal((await p.ctx.window.garudaLab.resetBench()).ok,false);
 assert.deepEqual(p.commands(),[]);
});
test('Gazebo mode waits for applied acknowledgement and disconnects the real-camera page',async()=>{
 const lab=labState({phase:'GROUND',armed:false,landed:true,source_kind:'fixed_stereo'});
 const p=labPage(labReply(()=>lab),{'garuda-lab-link':'1'});
 await settle();await p.poll();p.calls.length=0;
 const pending=p.ctx.window.garudaLab.setTestMode('gazebo');await settle();
 assert.equal(p.ctx.window.garudaLab.state().linked,true);
 lab.source_kind='gazebo';lab.control={owner:null,last_command:{id:'c1',applied:true}};
 await p.poll();await p.poll();await pending;
 assert.equal(p.ctx.window.garudaLab.state().linked,false);
 assert.deepEqual(p.commands(),['test_mode']);
});
test('real vehicle mode is refused even while grounded',async()=>{
 const p=labPage(labReply(labState({phase:'GROUND',armed:false,landed:true})),{'garuda-lab-link':'1'});
 await settle();await p.poll();p.calls.length=0;
 await assert.rejects(p.ctx.window.garudaLab.setTestMode('real'),/readiness/);
 assert.deepEqual(p.commands(),[]);
});

test('closing expanded depth releases the inset and rejects a response from before close',async()=>{
  const app=fs.readFileSync(path.join(__dirname,'app.js'),'utf8');
  const script=app.slice(app.indexOf('// Stereo depth map inset:'),app.indexOf('// The 3-D point cloud lives'));
  const requests=[],timers=new Map();
  let now=0,drawn=0;
  const events={addEventListener(event,fn){this[event]=fn;}};
  const elements={
    depthButton:{hidden:false,classList:{toggle(){}},setAttribute(){},focus(){}},
    depthPip:{hidden:true,width:640,height:360,getContext:()=>({drawImage(){drawn++;}})},
    depthRate:{},inspectDepth:{...events,hidden:true},depthInspector:{...events,open:false}
  };
  const ctx=vm.createContext({$:id=>elements[id],document:{...events,hidden:false},
    MutationObserver:class{observe(){}},performance:{now:()=>now},AbortController,setTimeout,clearTimeout,
    setInterval(fn){timers.set(1,fn);return 1;},clearInterval(id){timers.delete(id);},
    fetch:()=>new Promise(resolve=>requests.push(resolve)),
    createImageBitmap:async()=>({close(){}})
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'depth-view.js'),'utf8'),ctx);
  vm.runInContext(script,ctx);
  const frame=seq=>({ok:true,headers:new Map([['X-Stereo-Pair','e:'+seq],['X-Depth-Age-Ms','0']]),blob:async()=>({})});
  const {depthButton,depthPip,inspectDepth,depthInspector}=elements;
  depthButton.onclick();requests[0](frame(1));await settle();
  assert.equal(depthPip.hidden,false);
  now+=500;timers.get(1)();
  depthInspector.close();
  assert.equal(timers.size,0);assert.equal(depthButton.textContent,'Depth map');
  assert.equal(depthPip.hidden,true);assert.equal(inspectDepth.hidden,true);
  depthButton.onclick();requests[1](frame(2));await settle();
  assert.equal(depthPip.hidden,true);assert.equal(drawn,1);
  requests[2](frame(3));await settle();
  assert.equal(depthPip.hidden,false);assert.equal(drawn,2);
  depthButton.onclick();assert.equal(timers.size,0);assert.equal(depthPip.hidden,true);
});

test('Land stays a request while PX4 reports flight, and telemetry loss clears the aircraft state',async()=>{
  const {labFlightStatus}=require('./controller');
  let lab=labState({phase:'FLYING',armed:true,landed:false,landed_fresh:true,nav_state:14,source_kind:'fixed_stereo',altitude:1.2});
  const p=labPage(labReply(()=>lab),{'garuda-lab-link':'1'});
  await p.poll();
  p.els.land.onclick();
  await settle();
  const status=()=>{
    const s=p.ctx.window.garudaLab.state();
    return labFlightStatus(s.lab,s.view,s.lastFlightRequest);
  };
  assert.match(status(),/Land requested · PX4 reports Offboard, armed, in air · 1.2 m/);
  lab={...lab,telemetry_fresh:false};
  await p.poll();
  assert.match(status(),/Land requested · Aircraft state unknown$/);
  assert.equal(p.view().altitude,null);
  assert.equal(p.view().mode,'UNKNOWN');
  lab={...lab,phase:'GROUND',telemetry_fresh:true,landed:true,armed:false,altitude:0,nav_state:18};
  await p.poll();
  assert.match(status(),/PX4 reports Land, disarmed, on ground/);
});

test('a timed-out HTTP command has unknown delivery',async()=>{
  const p=labPage((url,body)=>body?.command==='land'?new Error('timeout'):labReply(labState())(url,body),
    {'garuda-lab-link':'1'});
  await p.poll();
  p.els.land.onclick();await settle();
  assert.equal(p.ctx.window.garudaLab.state().lastFlightRequest.status,'delivery unknown');
});
