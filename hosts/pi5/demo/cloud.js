'use strict';
(function(){
  const $=id=>document.getElementById(id),canvas=$('cloud'),g=canvas.getContext('2d');
  const defaults={yaw:0.3,pitch:-0.18,zoom:1.8};
  const view={...defaults,pivot:null,raw:false};
  let image=null,points=null,epoch=null,dirty=false,drag=null,paused=false;

  function guides(){
    const projection=Garuda.cloudProjection(view,view.pivot,canvas.width,canvas.height);
    function line(a,b,color,label){
      const segment=projection.segment(a,b);
      if(!segment)return;
      const [p,q]=segment;
      g.strokeStyle=color;g.beginPath();g.moveTo(...p);g.lineTo(...q);g.stroke();
      if(label){g.fillStyle=color;g.fillText(label,q[0]+4,q[1]-4);}
    }
    g.save();g.font='bold 14px sans-serif';g.lineWidth=1;
    for(let x=-2;x<=2;x++)line([x,0,0],[x,0,5],'rgba(170,195,210,.38)');
    for(let z=0;z<=5;z++)line([-2,0,z],[2,0,z],'rgba(170,195,210,.38)');
    const origin=[0,0,0];g.lineWidth=2;
    line(origin,[1,0,0],'#ff9a85','+X');
    line(origin,[0,1,0],'#9ae2af','+Y');
    line(origin,[0,0,1],'#79caff','+Z');
    const marker=projection.segment(origin,origin);
    if(marker){
      const [x,y]=marker[0];g.fillStyle='white';g.beginPath();g.arc(x,y,4,0,2*Math.PI);g.fill();
      g.fillText('Camera',x+6,y+16);
    }
    g.restore();
  }
  function draw(){
    dirty=false;
    if(!image)return;
    Garuda.renderCloud(points,image.data,canvas.width,canvas.height,view);
    g.putImageData(image,0,0);
    if(points?.n&&$('guides').checked)guides();
  }
  function invalidate(){if(!dirty){dirty=true;requestAnimationFrame(draw);}}
  function resize(){
    const scale=Math.min(window.devicePixelRatio||1,2,1920/Math.max(1,innerWidth));
    canvas.width=Math.max(320,Math.round(innerWidth*scale));
    canvas.height=Math.max(180,Math.round(innerHeight*scale));
    image=g.createImageData(canvas.width,canvas.height);invalidate();
  }
  const viewer=new DepthViewer({
    url:()=>'/stereo_cloud.bin?step='+$('detail').value+'&sampling='+($('raw').checked?'grid':'observed'),
    period:()=>$('detail').value==='2'?1000:500,
    async decode(response){
      const cloud=Garuda.decodeCloud(await response.arrayBuffer());
      if(!cloud)throw Error('Unreadable cloud');
      const next=Garuda.cloudPoints(cloud);
      const key=response.headers.get('X-Stereo-Pair');
      const source=key.slice(0,key.lastIndexOf(':'));
      return {draw(){
        if(source!==epoch){view.pivot=null;epoch=source;}
        points=next;
        if(view.pivot===null)view.pivot=Garuda.cloudPivot(points);
        draw();
        return (cloud.offsets?'Observed cells':'Fixed grid')+' · '+points.n.toLocaleString()+' points';
      }};
    },
    clear(){points=null;draw();},
    status(text){$('status').textContent=text;}
  });
  function start(){if(!paused&&!document.hidden)viewer.start();}
  for(const [id,yaw,pitch] of [['front',0,0],['top',0,-Math.PI/2],['side',Math.PI/2,0]]){
    $(id).onclick=()=>{Object.assign(view,{yaw,pitch,zoom:1.8});invalidate();};
  }
  $('guides').onchange=invalidate;
  $('pause').onclick=()=>{
    paused=!paused;
    if(paused)viewer.stop(true);else{viewer.stop();start();}
    $('pause').textContent=paused?'Resume':'Pause';$('raw').disabled=$('detail').disabled=paused;
  };
  function samplingChanged(){viewer.stop();view.raw=$('raw').checked;start();}
  $('raw').onchange=$('detail').onchange=samplingChanged;
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&!paused)viewer.stop();else start();});
  canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);canvas.style.cursor='grabbing';};
  canvas.onpointermove=e=>{
    if(!drag)return;
    view.yaw+=(e.clientX-drag.x)*0.006;
    view.pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,view.pitch+(e.clientY-drag.y)*0.006));
    drag={x:e.clientX,y:e.clientY};invalidate();
  };
  canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;canvas.style.cursor='grab';};
  canvas.onwheel=e=>{e.preventDefault();view.zoom=Math.max(.3,Math.min(5,view.zoom*(e.deltaY>0?1.1:.9)));invalidate();};
  canvas.ondblclick=()=>{Object.assign(view,defaults,{pivot:Garuda.cloudPivot(points)});invalidate();};
  addEventListener('resize',resize);resize();start();
})();
