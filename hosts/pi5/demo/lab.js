'use strict';
// Optional link from this page to the PX4 + Gazebo lab running on the viewer's own computer.
// Off until the operator switches it on (the browser may ask for permission to reach a local
// program). While it is on, the lab's SIMULATED PX4 vehicle is this page's one and only
// vehicle: the map, the decision panel and every flight button show and command it, and the
// page's own illustration stays on the ground, unseen. If the lab cannot be reached the page
// says so and offers no movement commands; it never falls back to animating the illustration.
// The lab only accepts this page's origin if it was started with that origin allowed. Nothing
// here can reach a real flight controller: the lab itself refuses to run outside simulation.
(function(){
  const LAB='http://127.0.0.1:8099',link=new Garuda.LabLink(),unlinker=new Garuda.Unlinker();
  let on=false,lab=null,labAt=-Infinity,note='';
  // Set by an operator action in THIS page load (linking, or any command button). Without it the
  // page only watches: a reload or a lapsed lease never takes control of the lab by itself.
  let acted=false,mismatchHeld=false,connectRequested=false,modeChanging=false;
  // A new id for every load of this page, never stored: the lab leases control to one session,
  // and a reloaded or duplicated page must come back as an observer, not as the old owner.
  const session=(globalThis.crypto&&crypto.randomUUID)?crypto.randomUUID():'page-'+Math.random().toString(36).slice(2)+Date.now().toString(36);
  // contacted: during this link the lab has answered, or was sent a command. Remembered across a
  // reload (the session id is not). Once true, only a FRESH landed-and-disarmed state lets the
  // link be dropped: a lab that stops answering is unknown, not landed.
  let contacted=false;try{contacted=localStorage.getItem('garuda-lab-contact')==='1';}catch(e){}
  function touch(v){if(v===contacted)return;contacted=v;try{localStorage.setItem('garuda-lab-contact',v?'1':'0');}catch(e){}}
  // takeoff.pending: a take-off left this page and the lab's command queue is not yet known to
  // have worked past it, so a state that still says GROUND proves nothing. The lab works its
  // queue in order, so it is cleared by the lab reporting as its last applied or refused command
  // either the take-off itself or ANY command this page sent after the take-off's own request
  // had finished (a later command can overwrite the take-off's entry within one controller tick).
  const takeoff={pending:false,settled:true,ids:new Set(),settledAt:-Infinity};
  try{on=localStorage.getItem('garuda-lab-link')==='1';}catch(e){}
  const button=document.createElement('button');button.id='labLink';
  const status=document.createElement('p');status.id='labStatus';
  document.querySelector('.flightCommands').append(button);document.querySelector('.flightCommands').after(status);
  const active=()=>on&&lab!==null&&performance.now()-labAt<1500;
  const view=()=>Garuda.labView(on,active()?lab:null,session);
  const mayAuto=()=>{const v=view();return !!v&&v.can.auto&&(v.control==='mine'||acted);};
  function setOn(next){intent++;selecting=Promise.resolve(true);if(!next)heartbeat(false);touch(false);Object.assign(takeoff,{pending:false,settled:true,ids:new Set()});on=next;lab=null;labAt=-Infinity;note='';try{localStorage.setItem('garuda-lab-link',on?'1':'0');}catch(e){}V=view();vehicleChanged();}
  async function send(payload){
    touch(true);
    const isTakeoff=payload.command==='takeoff'||payload.command==='takeoff_follow';
    if(isTakeoff)Object.assign(takeoff,{pending:true,settled:false,ids:new Set()});
    const barrier=!isTakeoff&&takeoff.pending&&takeoff.settled;      // started after the take-off's request finished
    const done=id=>{if(isTakeoff){takeoff.settled=true;takeoff.settledAt=performance.now();}if((isTakeoff||barrier)&&takeoff.pending&&id)takeoff.ids.add(id);};
    try{const r=await fetch(LAB+'/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Garuda-Session':session},body:JSON.stringify(payload),signal:AbortSignal.timeout(2500)});
      if(r.ok){note='';let id=null;try{id=(await r.json()).id??null;}catch(e){}done(typeof id==='string'?id:null);return {ok:true,id};}
      let m='command rejected';try{m=(await r.json()).error||m;}catch(e){}note='PX4 lab: '+m;
      // A refusal by the lab's HTTP layer means the take-off was never queued.
      if(isTakeoff)Object.assign(takeoff,{pending:false,settled:true});
      return {ok:false,id:null};}
    catch(e){done(null);note='PX4 lab did not answer · command not delivered';return {ok:false,id:null};}
  }
  // While this tab holds the lab's control lease it says so once a second; if it stops (tab
  // closed, computer asleep) the lab holds by itself. Sent from a worker where there is one,
  // because a browser slows the timers of a covered or background tab far below once a second.
  // A heartbeat never claims control: the lab only renews the lease of its current owner.
  // At most one heartbeat is in flight, and each gives up after 0.9 s: a stalled lab cannot pile up requests.
  const beatSource='let t=null,busy=false;onmessage=e=>{clearInterval(t);t=null;if(e.data.on)t=setInterval(()=>{if(busy)return;busy=true;fetch(e.data.url,{method:"POST",headers:{"Content-Type":"application/json","X-Garuda-Session":e.data.session},body:"{}",signal:AbortSignal.timeout(900)}).catch(()=>{}).finally(()=>{busy=false;});},1000);};';
  let beating=false,beatWorker=null,beatTimer=null,beatBusy=false;
  try{if(typeof Worker==='function'&&typeof Blob==='function')beatWorker=new Worker(URL.createObjectURL(new Blob([beatSource],{type:'text/javascript'})));}catch(e){beatWorker=null;}
  function heartbeat(want){
    if(want===beating)return;beating=want;
    if(beatWorker){beatWorker.postMessage({on:want,url:LAB+'/api/heartbeat',session});return;}
    clearInterval(beatTimer);beatTimer=null;
    if(want)beatTimer=setInterval(()=>{if(beatBusy)return;beatBusy=true;fetch(LAB+'/api/heartbeat',{method:'POST',headers:{'Content-Type':'application/json','X-Garuda-Session':session},body:'{}',signal:AbortSignal.timeout(900)}).catch(()=>{}).finally(()=>{beatBusy=false;});},1000);
  }
  function render(){
    button.textContent=!on?'Link PX4 lab':unlinker.pending?'Unlinking · waiting for hold…':'PX4 lab: linked';button.className=on?'selected':'';button.disabled=unlinker.pending;button.title='';
    const text=unlinker.note||note;status.hidden=!text;status.textContent=text;
  }
  // Called by the page every frame, so loss of contact shows without waiting for the next poll.
  window.labFrame=()=>{V=view();};
  async function poll(){
    if(on){
      try{const r=await fetch(LAB+'/api/state',{signal:AbortSignal.timeout(1000)});const s=await r.json();if(s&&s.phase){lab=s;labAt=performance.now();touch(true);
        if(takeoff.pending){const lc=s.control?.last_command;
          if(s.control?(!!lc&&takeoff.ids.has(lc.id)):(takeoff.settled&&labAt>takeoff.settledAt))takeoff.pending=false;}}}
      catch(e){if(performance.now()-labAt>1500)lab=null;}
      if(unlinker.pending){if(unlinker.update(view(),labAt,performance.now(),takeoff.pending).unlink)setOn(false);}
      else if(connectRequested&&active()&&view().fresh){
        connectRequested=false;const kind=Garuda.labSourceFor(Garuda.capabilities(S));
        if(kind&&lab.source_kind!==kind&&view().control!=='other'&&lab.phase==='GROUND'&&lab.armed===false)send({command:'source',kind});
      }
      else if(active()&&view().control==='mine'&&lab.source_kind!==Garuda.labSourceFor(Garuda.capabilities(S))&&lab.armed===true){if(!mismatchHeld){intent++;send({command:'hold'});mismatchHeld=true;}note='Camera placement or source changed. Land before reconnecting.';}
      else if(!modeChanging&&active()&&mayAuto())for(const c of link.sync(lab,performance.now(),F.id,S))send(c);
      if(active()&&lab.source_kind===Garuda.labSourceFor(Garuda.capabilities(S)))mismatchHeld=false;
      heartbeat(active()&&view().control==='mine');
    }
    render();setTimeout(poll,on?300:1000);
  }
  function askUnlink(){
    const r=unlinker.request(view(),performance.now(),{contacted,takeoffPending:takeoff.pending});
    if(r.send)send(r.send).then(a=>{unlinker.sent(a.ok,a.id);render();});
    if(r.unlink)setOn(false);
  }
  button.onclick=()=>{
    intent++;        // linking or asking to unlink is an operator action: anything still waiting is dropped
    if(!on){unlinker.note='';setOn(true);acted=true;connectRequested=true;render();return;}
    askUnlink();render();
  };
  window.garudaLab={
    state:()=>({linked:on,pending:unlinker.pending,view:view(),lab:active()?lab:null}),
    connect:()=>{if(!on){intent++;unlinker.note='';setOn(true);acted=true;connectRequested=true;render();}},
    disconnect:()=>{intent++;askUnlink();render();},
    setTestMode:async(mode)=>{
      if(!active()||!lab.telemetry_fresh||lab.armed!==false||lab.landed!==true||lab.phase!=='GROUND')throw Error('Land and disarm before changing test mode');
      if(!['bench','gazebo'].includes(mode))throw Error('Real vehicle readiness checks are not integrated');
      acted=true;intent++;modeChanging=true;
      try {
      const ack=await send({command:'test_mode',mode});
      if(!ack.ok||!ack.id)throw Error('Mode change was not queued');
      for(let i=0;i<30;i++){
        await new Promise(r=>setTimeout(r,100));
        const last=lab?.control?.last_command;
        if(last?.id===ack.id){
          if(!last.applied)throw Error(last.error||'Mode change refused');
          if(mode==='gazebo'){setOn(false);render();}
          return;
        }
      }
      throw Error('Mode change not confirmed; inspect the laptop lab before continuing');
      } finally {modeChanging=false;}
    },
    resetBench:async()=>{
      if(!active()||!lab.telemetry_fresh||lab.armed!==false||lab.landed!==true||lab.phase!=='GROUND')return {ok:false};
      acted=true;intent++;return send({command:'bench_reset'});
    }

  };
  async function piTransmit(target){
    const r=await fetch('/api/transmit?target='+target,{method:'POST',signal:AbortSignal.timeout(1500)});
    const j=await r.json();if(!r.ok||!j.ok)throw Error(j.reason||'Camera command refused');
  }
  async function onboardAction(command){
    const token=++intent, id=F.id, epoch=S?.frame?.epoch, instance=S?.stats?.instance;
    const same=()=>token===intent&&on&&active()&&Garuda.mountOf(S).mount==='vehicle'&&S?.frame?.epoch===epoch&&S?.stats?.instance===instance&&F.id===id;
    try{
      if(['takeoff','follow'].includes(command)&&lab?.source_kind!=='pi_onboard')throw Error('Waiting for the on-drone camera input in PX4');
      if(command==='follow'){
        if(id===null)throw Error('Select a person first');
        if(!(await selecting)||!same())return;
        if(S?.lock?.id!==id)throw Error('Waiting for the camera to confirm your selection');
        // A new, explicit Follow press opens the perception grant; no hidden auto-resume.
        await piTransmit('off');if(!same())return;
        const a=await send({command:'follow'});if(!a.ok||!same())return;
        await piTransmit('sim');
        if(!same())await piTransmit('off');
      }else{
        // Hold/Land go to the controller even if the Pi is unreachable.
        const a=await send({command});
        if(command==='hold'||command==='land')try{await piTransmit('off');}catch(e){note='PX4 '+command+' requested; camera: '+e.message;}
        if(!a.ok)render();
      }
    }catch(e){note=e.message;render();}
  }
  // ---- operator intent ---------------------------------------------------------------------
  // Every flight action of the operator, and every link transition, takes a new intent number.
  // Anything that had to wait (for a selection to be accepted by the lab) checks, after each
  // wait, that it is still the latest intent and that it is still about the same person on the
  // same camera run, detector process and lab source. So a Follow that was waiting can never
  // be sent after a later Hold, Land, Release, unlink or change of person.
  let intent=0;
  // The outcome of the selection this page last sent by a click on a box: true, or false when
  // the lab or camera refused it. A refusal stands until a new click succeeds.
  let selecting=Promise.resolve(true);
  // Linked: a button commands the lab and nothing else. Not linked: it does what it always did.
  // Land and Hold are sent even without contact (best effort; a failure is shown).
  const route=(id,payload,always=false)=>{const b=$(id),old=b.onclick;b.onclick=e=>{if(!on){if(old)old.call(b,e);return;}intent++;if(always||active()){acted=true;if(Garuda.mountOf(S).mount==='vehicle')onboardAction(payload().command);else send(payload());}};};
  // Send a command that acts on the person selected on this page (needPerson), or that takes
  // the person along if there is one (take-off), only once the lab holds exactly that person.
  async function guarded(command,needPerson){
    if(!active())return;acted=true;
    const token=++intent,id=F.id??null,epoch=S?.frame?.epoch,instance=S?.stats?.instance,kind=Garuda.labSourceFor(Garuda.capabilities(S));
    const same=()=>token===intent&&on&&active()&&(F.id??null)===id&&S?.frame?.epoch===epoch&&S?.stats?.instance===instance&&Garuda.labSourceFor(Garuda.capabilities(S))===kind;
    const stop=text=>{if(text&&token===intent){note=text;render();}};
    if(id===null){if(needPerson)return stop('Select a person on this page first');send(command(null));return;}
    const accepted=await selecting;if(!same())return;
    if(accepted===false)return stop('The selection was refused · click your box again');
    const want=link.selection(lab,id,S);
    if(want){const a=await send(want);if(!a.ok){render();return;}if(!same())return;}
    else if(lab.target_id!==id||lab.requires_reselection||lab.source_kind!==kind)return stop('The PX4 lab does not have this page\'s person · click your box again');
    send(command(id));
  }
  // Take off: the lab must first be on this camera's source; that switch clears its selection,
  // so it is not chained into a take-off: the operator presses Take off again once it shows.
  {const b=$('takeoff'),old=b.onclick;b.onclick=async e=>{if(!on){if(old)old.call(b,e);return;}if(!active())return;
    acted=true;
    {const pre=link.sync(lab,performance.now(),null,S).find(c=>c.command==='source');
     if(pre){intent++;if((await send(pre)).ok)note='PX4 lab switched to this camera · press Take off again';render();return;}}
    if(Garuda.mountOf(S).mount==='vehicle')return onboardAction('takeoff');
    return guarded(()=>({command:'takeoff'}),false);};}
  route('land',()=>({command:'land'}),true);route('pause',()=>({command:'hold'}),true);
  for(const [id,name]of[['followTarget','follow'],['circleTarget','circle']]){const b=$(id),old=b.onclick;b.onclick=e=>{if(!on){if(old)old.call(b,e);return;}if(Garuda.mountOf(S).mount==='vehicle'){if(name==='follow')return onboardAction('follow');return;}return guarded(()=>({command:name}),true);};}
  // Releasing the target and the sliders apply to the page and, when linked, to the lab as well.
  {const b=$('reset'),old=b.onclick;b.onclick=e=>{if(old)old.call(b,e);intent++;if(active())send({command:'release'});};}
  {const b=$('tuningReset'),old=b.onclick;b.onclick=e=>{if(old)old.call(b,e);if(active()){acted=true;send(link.settings(F.settings));}};}
  for(const name of ['gap','radius','speed']){const input=$(name+'Setting'),old=input.oninput;
    input.oninput=e=>{if(old)old.call(input,e);if(active())send(link.settings(F.settings));};}
  // Only an operator's own selection is passed on; a re-match made by the page is not.
  const pageSelect=window.select;
  window.select=function(id){pageSelect(id);intent++;if(active()&&S&&F.id===id&&Garuda.mountOf(S).mount!=='vehicle'){acted=true;selecting=send({command:'select',id,epoch:S.frame.epoch}).then(a=>a.ok,()=>false);}};
  V=view();poll();
})();
