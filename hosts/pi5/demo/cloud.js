'use strict';
// Full-window 3-D view of the current stereo frame. One viewpoint; a picture, not a measurement.
// Points are drawn as squares big enough to touch their neighbours: that closes the dot grid,
// it does not recover surfaces the camera could not see.
(function(){const $=id=>document.getElementById(id),cv=$('cloud'),g=cv.getContext('2d');
 const DEFAULT={yaw:0.3,pitch:-0.15,zoom:1.3};const view={...DEFAULT},gate=new Garuda.LiveGate(2500,3000),disp=new Garuda.LiveDisplay(gate);
 let img=null,pts=null,dirty=false,drag=null,ctl=null,last=0,shown='';
 function resize(){const dpr=Math.min(2,window.devicePixelRatio||1),s=Math.min(dpr,1920/Math.max(1,innerWidth));cv.width=Math.max(320,Math.round(innerWidth*s));cv.height=Math.max(180,Math.round(innerHeight*s));img=g.createImageData(cv.width,cv.height);invalidate();}
 function note(text){g.fillStyle='#ffbf69';g.font='16px sans-serif';g.fillText(text,20,70);}
 function draw(){dirty=false;if(!img)return;const n=Garuda.renderCloud(pts,img.data,cv.width,cv.height,view);g.putImageData(img,0,0);
  if(!n)note(shown==='waiting'?'Waiting for a fresh stereo frame…':'No live stereo data');}
 function invalidate(){if(!dirty){dirty=true;requestAnimationFrame(draw);}}
 // Runs four times a second whatever the network is doing: decides what the page may claim.
 function watchdog(){const now=Date.now();if(ctl&&gate.timedOut(now)){ctl.abort();ctl=null;}
  const w=disp.watch(now);if(w.clear&&pts){pts=null;invalidate();}        // never leave old geometry looking live
  if(w.text!==$('status').textContent)$('status').textContent=w.text;
  if(w.state!==shown){shown=w.state;invalidate();}
  const every=$('detail').value==='2'?1000:500;
  if(!disp.paused&&!document.hidden&&now-last>=every-20){last=now;tick();}}
 async function tick(){const now=Date.now(),token=gate.begin(now);if(token===null)return;
  ctl=new AbortController();const mine=ctl;
  try{const r=await fetch('/stereo_cloud.bin?step='+$('detail').value+'&t='+now,{cache:'no-store',signal:mine.signal});
   if(!r.ok){disp.failure(token,r.status===404?'stereo is switched off on the camera':'no fresh stereo frame');return;}
   const c=Garuda.decodeCloud(await r.arrayBuffer()),p=c?Garuda.cloudPoints(c):null;
   if(!p){disp.failure(token,'unreadable data from the camera');return;}
   // success() refuses late, cancelled, timed-out and paused replies: they never reach the screen
   if(disp.success(token,Date.now(),p.n.toLocaleString()+' points · frame '+(r.headers.get('X-Stereo-Pair')||'').split(':').pop())){pts=p;invalidate();}}
  catch(e){disp.failure(token,'');}
  finally{if(ctl===mine)ctl=null;}}
 $('front').onclick=()=>{Object.assign(view,{yaw:0,pitch:0,zoom:1});invalidate();};
 $('top').onclick=()=>{Object.assign(view,{yaw:0,pitch:-1.35,zoom:1.6});invalidate();};
 $('side').onclick=()=>{Object.assign(view,{yaw:1.35,pitch:0,zoom:1.6});invalidate();};
 $('pause').onclick=()=>{if(disp.paused){disp.resume();if(ctl){ctl.abort();ctl=null;}last=0;}else disp.pause();
  $('pause').textContent=disp.paused?'Resume':'Pause';watchdog();};
 $('detail').onchange=()=>{last=0;};
 document.addEventListener('visibilitychange',()=>{if(!document.hidden){last=0;watchdog();}});
 cv.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY};cv.setPointerCapture(e.pointerId);cv.style.cursor='grabbing';};
 cv.onpointermove=e=>{if(!drag)return;view.yaw+=(e.clientX-drag.x)*0.006;view.pitch=Math.max(-1.5,Math.min(1.5,view.pitch+(e.clientY-drag.y)*0.006));drag={x:e.clientX,y:e.clientY};invalidate();};
 cv.onpointerup=cv.onpointercancel=()=>{drag=null;cv.style.cursor='grab';};
 cv.onwheel=e=>{e.preventDefault();view.zoom=Math.max(0.3,Math.min(5,view.zoom*(e.deltaY>0?1.1:0.9)));invalidate();};
 cv.ondblclick=()=>{Object.assign(view,DEFAULT);invalidate();};
 addEventListener('resize',resize);resize();watchdog();setInterval(watchdog,250);
})();
