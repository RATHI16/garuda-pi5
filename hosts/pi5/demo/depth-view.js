'use strict';
// The age header uses the SDK's RGB timestamp in the Pi monotonic clock domain.
// Adding the complete request interval gives a conservative age in this browser.
class PairFreshness {
  constructor(start, limit=2000){
    this.start=start;this.limit=limit;this.epoch=null;this.seq=-1;this.retired=new Set();
    this.displayed=[];this.at=null;this.age=null;
  }
  next(key){
    const match=/^(.+):(\d+)$/.exec(key||'');
    if(!match)return null;
    const epoch=match[1],seq=Number(match[2]);
    if(!Number.isSafeInteger(seq)||this.retired.has(epoch)||(epoch===this.epoch&&seq<=this.seq))return null;
    return {epoch,seq};
  }
  accept(pair,header,start,now,draw){
    const known=header!==null;
    if(known&&!/^\d+$/.test(header))return false;
    const age=(known?Number(header):0)+now-start;
    if(!Number.isFinite(age)||age<0||age>=this.limit)return false;
    draw();
    if(this.epoch!==null&&this.epoch!==pair.epoch){
      this.retired.add(this.epoch);this.displayed=[];this.start=now;
    }
    this.epoch=pair.epoch;this.seq=pair.seq;this.age=known?age:null;
    this.at=now;this.expires=now+this.limit-age;this.displayed.push(now);
    return true;
  }
  state(now){
    this.displayed=this.displayed.filter(t=>now-t<4000);
    const rate=now-this.start<4000?'—':(this.displayed.length/4).toFixed(2);
    const stale=this.at!==null&&now>=this.expires;
    const age=this.age===null?'age unknown':'age ≤ '+Math.ceil(this.age+now-this.at)+' ms';
    const label=this.at===null?'Waiting for depth':stale?'Stale depth':'Pair '+this.seq+' · '+age;
    return {stale,text:label+' · '+rate+' displayed pairs/s'};
  }
}

// One timed request at a time. A decoded frame is committed only after its response,
// generation and age have been checked. The watchdog also runs during a stalled request.
class DepthViewer {
  constructor({url,period,decode,clear,status}){
    Object.assign(this,{url,period,decode,clear,status});this.timer=null;this.request=null;
  }
  start(){
    if(this.timer!==null)return;
    this.clock=new PairFreshness(performance.now());this.last=-Infinity;this.info='';this.error='';this.visible=false;
    this.timer=setInterval(()=>this.tick(),250);this.tick();
  }
  stop(keep=false){
    clearInterval(this.timer);this.timer=null;
    if(this.request)this.request.abort();this.request=null;
    if(!keep){this.clear();this.visible=false;}
    else this.status(this.visible?'Paused · '+this.info+' · pair '+this.clock.seq:'Paused · no depth frame');
  }
  tick(){
    const now=performance.now(),s=this.clock.state(now);
    if(s.stale&&this.visible){this.clear();this.visible=false;}
    this.status((this.visible&&this.info?this.info+' · ':'')+s.text+(!this.visible&&this.error?' · '+this.error:''));
    if(!this.request&&now-this.last>=this.period()-10){this.last=now;this.load(now);}
  }
  async load(start){
    const req=new AbortController();this.request=req;
    const timeout=setTimeout(()=>req.abort(),2200);
    let frame;
    try{
      const response=await fetch(this.url(),{cache:'no-store',signal:req.signal});
      if(!response.ok)throw Error(response.status===404?'Stereo unavailable':'Waiting for a fresh pair');
      if(this.request!==req)return;
      const pair=this.clock.next(response.headers.get('X-Stereo-Pair'));
      if(!pair){await response.body?.cancel();return;}
      frame=await this.decode(response);
      if(this.request!==req||req.signal.aborted)return;
      if(this.clock.accept(pair,response.headers.get('X-Depth-Age-Ms'),start,performance.now(),()=>{this.info=frame.draw()||'';})){
        this.visible=true;this.error='';
        this.status((this.info?this.info+' · ':'')+this.clock.state(performance.now()).text);
      }else this.error='Frame age unavailable or too old';
    }catch(e){if(this.request===req)this.error=req.signal.aborted?'Request timed out':e.message;}
    finally{clearTimeout(timeout);frame?.release?.();if(this.request===req)this.request=null;}
  }
}

async function depthBitmap(response,draw){
  const bmp=await createImageBitmap(await response.blob());
  return {draw:()=>draw(bmp,response.headers),release:()=>bmp.close()};
}
if(typeof module!=='undefined')module.exports={PairFreshness,DepthViewer,depthBitmap};
