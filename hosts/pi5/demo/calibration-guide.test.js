const test=require('node:test'),assert=require('node:assert/strict');
const {patternGeometry,nextStep}=require('./calibration-guide.js');
test('checkerboard dimensions mean inner corners, not squares',()=>{
 const p=patternGeometry('checkerboard',9,6);assert.equal(p.width,10);assert.equal(p.height,7);assert.equal(p.points.length,54);assert.deepEqual(p.points[0],[1,1]);assert.deepEqual(p.points.at(-1),[9,6]);
});
test('offset circles use doubled same-row pitch',()=>{
 const p=patternGeometry('asymmetric_circles',3,2);assert.deepEqual(p.points,[[0,0],[2,0],[4,0],[1,1],[3,1],[5,1]]);
});
const good=(split='tune')=>({split,pattern_found:true,has_depth:true,distance_m:2,board_depth:{median_m:2}});
test('depth guide rejects missing pattern, distance or usable board depth as progress',()=>{
 const s=nextStep({goal:'depth'},[{...good(),pattern_found:false},{...good(),distance_m:null},{...good(),board_depth:{median_m:null}}]);assert.equal(s.tune,0);assert.equal(s.split,'tune');
});
test('depth guide moves to independent checks without claiming accuracy',()=>{
 let c=[good(),good(),good()];assert.equal(nextStep({goal:'depth'},c).split,'validate');
 c.push(good('validate'),good('validate'),good('validate'));assert.match(nextStep({goal:'depth'},c).instruction,/not an accuracy pass/);
});
test('lens guide uses twelve tuning views',()=>{
 assert.equal(nextStep({goal:'lens'},Array.from({length:11},()=>good())).split,'tune');
 assert.equal(nextStep({goal:'lens'},Array.from({length:12},()=>good())).split,'validate');
});
