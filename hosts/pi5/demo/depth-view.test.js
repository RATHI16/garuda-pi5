'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {PairFreshness}=require('./depth-view');
const {cloudProjection,cloudPivot}=require('./controller');

test('repeated or out-of-order pairs cannot keep a stalled view alive',()=>{
  const clock=new PairFreshness(0);
  const add=(key,header,start,now)=>clock.accept(clock.next(key),header,start,now,()=>{});
  assert.ok(add('a:5','100',20,25));
  assert.match(clock.state(225).text,/age ≤ 305 ms/);
  assert.equal(clock.next('a:5'),null);assert.equal(clock.next('a:4'),null);
  assert.ok(clock.state(1920).stale);assert.match(clock.state(4100).text,/0.00 displayed/);
  assert.ok(add('b:0',null,4200,4210));
  assert.match(clock.state(4210).text,/age unknown/);assert.equal(clock.next('a:8'),null);
});

test('invalid ages, delayed responses and failed draws never advance the displayed sequence',()=>{
  const clock=new PairFreshness(0),pair=clock.next('a:3');
  for(const header of ['-1','','NaN','2000'])assert.equal(clock.accept(pair,header,0,1,assert.fail),false);
  assert.equal(clock.accept(pair,null,0,2001,assert.fail),false);
  assert.throws(()=>clock.accept(pair,'0',0,10,()=>{throw Error('bad bitmap');}),/bad bitmap/);
  assert.deepEqual(clock.next('a:3'),pair);
});

test('one metre scales with perspective and clipping works from inside the guide grid',()=>{
  const pivot=cloudPivot({n:3,xyz:[0,0,1,0,0,2,0,0,4]});
  const p=cloudProjection({yaw:0,pitch:0,zoom:1},pivot,400,300);
  assert.deepEqual(p.segment([0,0,1],[1,0,1]),[[200,150],[500,150]]);
  const inside=cloudProjection({yaw:0,pitch:0,zoom:.5},pivot,400,300);
  assert.equal(inside.segment([0,0,0],[1,0,.5]),null);
  const clipped=inside.segment([0,0,0],[1,0,2]);
  assert.ok(clipped.flat().every(Number.isFinite));assert.ok(clipped[0][0]>clipped[1][0]);
});
