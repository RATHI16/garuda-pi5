'use strict';
// One operator workflow. The existing controller remains the only simulator publisher.
(function(){
  const section=document.createElement('section');section.className='flightSetup';
  section.innerHTML=`<div class="setupChoices"><label>Camera <strong id="setupCamera">Connecting…</strong></label><label id="placementSlot">Placement </label><label>Control destination <select id="flightDestination"><option value="preview">Page simulation</option><option value="sim">PX4 on this laptop</option><option value="fc" disabled>Real flight controller · not ready</option></select></label><button id="connectFlight" class="primary">Start PX4 lab</button><button id="disconnectFlight" hidden>Disconnect</button></div><p><label>Test mode <select id="testMode"><option value="bench">Bench · bounded yaw</option><option value="gazebo">Gazebo · closed loop</option><option value="real" disabled>Real vehicle · readiness checks required</option></select></label> <button id="applyTestMode">Apply mode</button> <strong id="benchMode">Bench camera → simulation</strong> <span id="benchBudget"></span> <button id="resetBenchTrial">Reset bench trial</button> <a href="http://127.0.0.1:8099" target="_blank" rel="noopener">Test modes: bench / Gazebo / real-vehicle readiness</a></p><p id="flightEvidence" hidden></p><p id="flightSetupStatus" role="status">Choose where the controls should send commands.</p>`;
  const modeDetails=document.createElement('details');modeDetails.className='testModeDetails';
  const modeSummary=document.createElement('summary');modeSummary.textContent='Test modes & turn limits';
  modeDetails.append(modeSummary,section.querySelector('p'));section.append(modeDetails);
  document.querySelector('.stage').before(section);
  const mount=$('mountSelect');$('placementSlot').append(mount);
  mount.options[0].textContent='Fixed camera';mount.options[1].textContent='On drone';
  const destination=$('flightDestination'),connect=$('connectFlight'),disconnect=$('disconnectFlight'),status=$('flightSetupStatus');
  destination.value=window.garudaLab.state().linked?'sim':'preview';
  let busy=false,error='',changing=false;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  async function disconnected(){
    const st=window.garudaLab.state();
    if(!st.linked)return;
    if(!st.view?.fresh||st.view.airborne!==false)throw Error('Land in PX4 before changing placement or destination.');
    window.garudaLab.disconnect();
    for(let i=0;i<50&&window.garudaLab.state().linked;i++)await sleep(100);
    if(window.garudaLab.state().linked)throw Error('Waiting for PX4 to disconnect');
  }
  destination.onchange=async()=>{
    const want=destination.value;busy=true;error='';
    try{await disconnected();if(S?.transmit?.request&&S.transmit.request!=='off'){
      const r=await fetch('/api/transmit?target=off',{method:'POST',signal:AbortSignal.timeout(1500)});const j=await r.json();if(!r.ok||!j.ok)throw Error(j.reason||'Could not stop the previous destination');}
      destination.value=want;
    }catch(e){error=e.message;destination.value=window.garudaLab.state().linked?'sim':'preview';}
    finally{busy=false;window.renderFlightSetup();}
  };
  mount.onchange=async()=>{
    const wanted=mount.value;busy=true;changing=true;error='';
    try{await disconnected();const r=await fetch('/api/mount?value='+wanted,{method:'POST',signal:AbortSignal.timeout(2000)}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.reason||'Placement change refused');F.reset();}
    catch(e){error=e.message;mount.value=Garuda.mountOf(S).mount;}
    finally{busy=false;changing=false;mount.blur();window.renderFlightSetup();}
  };
  connect.onclick=async()=>{
    if(busy)return;busy=true;error='';destination.value='sim';window.renderFlightSetup();
    try{
      // Reuse a running controller; never start a second publisher just because a fetch failed.
      let running=false;
      try{const r=await fetch('http://127.0.0.1:8099/api/state',{signal:AbortSignal.timeout(1000)});running=r.ok&&(await r.json()).phase!==undefined;}catch(e){}
      if(!running){const r=await fetch('http://127.0.0.1:8100/api/lab/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(30000)});const j=await r.json();if(!r.ok)throw Error(j.error||'PX4 could not start');}
      window.garudaLab.connect();
    }catch(e){error='PX4 did not connect: '+e.message+'. Open the laptop launcher at localhost:8100 to check setup.';}
    finally{busy=false;window.renderFlightSetup();}
  };
  $('applyTestMode').onclick=async()=>{
    busy=true;error='';try{
      if($('testMode').value==='bench'&&Garuda.mountOf(S).mount!=='vehicle')throw Error('Choose On drone placement for the bench yaw test');
      await window.garudaLab.setTestMode($('testMode').value);F.reset();
      if($('testMode').value==='gazebo')error='Gazebo camera selected. Open the laptop lab below to select its person and use its flight controls. This camera page is disconnected.';
    }catch(e){error=e.message;}finally{busy=false;window.renderFlightSetup();}
  };
  $('resetBenchTrial').onclick=async()=>{
    busy=true;error='';try{const r=await window.garudaLab.resetBench();if(!r.ok)throw Error('Land and disarm before resetting the bench trial');F.reset();}catch(e){error=e.message;}finally{busy=false;window.renderFlightSetup();}
  };
  disconnect.onclick=()=>{window.garudaLab.disconnect();window.renderFlightSetup();};
  // Transport details and bench tools remain inspectable, outside the normal flight flow.
  const advanced=document.createElement('details');advanced.className='operator';advanced.innerHTML='<summary>Advanced tuning & diagnostics</summary>';
  document.querySelector('.bottom').after(advanced);
  // The demo's own controls stay where they always were, in plain view: follow gap / travel /
  // speed sliders, follow position, gesture control, face-confirmed return, face enrolment and
  // the gesture readout (owner, 2026-11-16: these must not disappear). Only transport plumbing
  // and bench tools go under Advanced.
  advanced.append($('sendRow'),$('fault'),$('resume'),$('labStatus'),$('labLink'));
  const historyButton=document.createElement('button'),historyText=document.createElement('p');
  historyButton.textContent='Read QGC link history';historyText.setAttribute('role','status');
  advanced.append(historyButton,historyText);
  historyButton.onclick=async()=>{
    historyButton.disabled=true;historyText.textContent='Reading the laptop simulator’s QGC recording…';
    try{
      const r=await fetch('http://127.0.0.1:8100/api/lab/qgc-link',{signal:AbortSignal.timeout(10000)}),j=await r.json();
      if(!r.ok||!Array.isArray(j.events))throw Error(j.error||'Laptop launcher update required.');
      const lines=j.events.map(e=>new Date(e.received).toLocaleString()+' · '+e.event);
      const sent=window.garudaLab.state().lab?.companion_presence?.sent;
      if(Number.isInteger(sent))lines.push('Laptop sender at '+new Date().toLocaleTimeString()+': '+sent+' heartbeats sent');
      const reception=j.events.length?'Last received by QGC: ':'No link events recorded by QGC in this session. ';
      historyText.textContent=reception+lines.join(' / ');
    }catch(e){historyText.textContent='QGC history unavailable. '+e.message;}
    finally{historyButton.disabled=false;}
  };
  $('labLink').hidden=true;
  const actions=document.querySelector('.flightCommands');actions.append($('pause'));$('followTarget').textContent='Follow';
  const oldHold=$('pause').onclick,oldFollow=$('followTarget').onclick;
  $('pause').onclick=e=>{if(!window.garudaLab.state().linked){F.paused=true;F.goodSince=null;}else oldHold(e);};
  $('followTarget').onclick=e=>{if(!window.garudaLab.state().linked)F.paused=false;return oldFollow(e);};
  window.renderFlightSetup=()=>{
    const st=window.garudaLab.state(),v=st.view,m=Garuda.mountOf(S),linked=st.linked;
    $('setupCamera').textContent=Garuda.cameraLabel(S?.frame?.source,Garuda.capabilities(S).distance==='stereo');
    connect.hidden=destination.value!=='sim'||linked;connect.disabled=busy;connect.textContent=busy?'Connecting…':'Start PX4 lab';disconnect.hidden=!linked;disconnect.disabled=busy||st.pending;
    destination.disabled=busy||linked&&(!v?.fresh||v.airborne!==false);
    mount.disabled=busy||m.locked||linked&&(!v?.fresh||v.airborne!==false);if(!changing&&document.activeElement!==mount)mount.value=m.mount;
    const onboard=linked&&m.mount==='vehicle';
    const trial=st.lab?.bench_trial;
    $('applyTestMode').disabled=$('testMode').disabled=busy||!trial||!st.lab?.telemetry_fresh||st.lab.armed!==false||st.lab.landed!==true||st.lab.phase!=='GROUND';

    $('benchMode').textContent=onboard?'Bench test · bounded yaw only':'Flight test modes';
    $('resetBenchTrial').hidden=!onboard;
    $('resetBenchTrial').disabled=busy||!trial||!st.lab?.telemetry_fresh||st.lab.armed!==false||st.lab.landed!==true||st.lab.phase!=='GROUND';
    $('benchBudget').textContent=onboard?(trial?`${trial.turned_deg.toFixed(1)}° / ${trial.max_degrees.toFixed(0)}° · ${trial.elapsed_s.toFixed(1)} / ${trial.max_seconds} s · ${trial.reason||'Automatic Hold at either limit'}`:'Controller update needed; bench limits are not active'):'Switch modes in the laptop lab while landed and disarmed. Disconnect this camera page before using the Gazebo camera.';

    const evidence=$('flightEvidence');
    evidence.hidden=!linked;
    if(linked)evidence.textContent=Garuda.labFlightStatus(st.lab,v,st.lastFlightRequest);
    status.textContent=error||(busy?'Connecting…':linked?(!v?.contact?'Waiting for PX4 on this laptop…':v.reason||'PX4 on laptop is connected.'):destination.value==='sim'?'Start the lab to connect Gazebo, PX4 and the flight controls.':'Page simulation only · no flight commands leave this page.');
    if(onboard)status.textContent+=' · Real camera input; simulated vehicle. The bench camera does not move with it.';
    $('circleTarget').hidden=onboard; $('pause').hidden=false;$('pause').textContent='Hold';
    if(linked&&v?.kind!==Garuda.labSourceFor(Garuda.capabilities(S))){$('takeoff').disabled=true;$('followTarget').disabled=true;status.textContent=error||'Preparing the camera input for PX4… '+(v?.reason||'');}
    if(onboard){$('followTarget').disabled=v?.kind!=='pi_onboard'||!v?.can.follow||F.id===null;$('takeoff').disabled=v?.kind!=='pi_onboard'||!v?.can.takeoff;$('land').disabled=!v?.can.land;$('pause').disabled=!v?.can.hold;}
    if(linked&&F.id==null){$('followTarget').disabled=true;if(v?.fresh&&!error)status.textContent='No target selected · '+status.textContent;}
    if(onboard&&trial?.exhausted)$('followTarget').disabled=true;
    if(!linked&&destination.value==='sim')for(const id of ['takeoff','followTarget','land','pause'])$(id).disabled=true;
    if(!linked&&destination.value==='preview'&&m.mount==='vehicle')status.textContent=error||'Camera on drone · preview only. Choose PX4 on this laptop and Start PX4 lab to fly in simulation.';
  };
  window.renderFlightSetup();
})();
