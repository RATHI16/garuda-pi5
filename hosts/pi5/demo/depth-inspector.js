'use strict';
(function(){
  const dialog=document.getElementById('depthInspector');
  const canvases=['inspectRgb','inspectMap'].map(id=>document.getElementById(id));
  const reading=document.getElementById('inspectReading'),status=document.getElementById('inspectFrame');
  const viewer=new DepthViewer({
    url:()=>'/stereo_depth.jpg?paired=1',period:()=>500,
    decode:r=>depthBitmap(r,(bitmap,headers)=>{
      if(bitmap.width!==1280||bitmap.height!==360)throw Error('Unexpected paired image size');
      canvases.forEach((c,i)=>c.getContext('2d').drawImage(bitmap,i*640,0,640,360,0,0,640,360));
      const matched=headers.get('X-Stereo-Outline')==='matched',z=Number(headers.get('X-Depth-Z-M'));
      reading.textContent=matched?(z>0?'Torso depth ≈ '+z.toFixed(2)+' m · white outline':'Measured region outlined · distance unavailable'):'No matched measurement region on this pair';
    }),
    clear:()=>{for(const c of canvases)c.getContext('2d').clearRect(0,0,c.width,c.height);reading.textContent='';},
    status:text=>{status.textContent=text;}
  });
  document.getElementById('inspectDepth').onclick=()=>{dialog.showModal();viewer.start();};
  dialog.addEventListener('close',()=>viewer.stop());
  document.addEventListener('visibilitychange',()=>{if(document.hidden)viewer.stop();else if(dialog.open)viewer.start();});
})();
