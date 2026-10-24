const {test}=require('node:test');const assert=require('node:assert/strict');
const {EditHistory,boundedWorkspace,cloneEdit,applyGlobalPreset}=require('../web/history.js');
test('history retains baseline, coalesces a slider gesture and branches after undo',()=>{
 const h=new EditHistory({exposure:0},'natural');
 h.capture({exposure:.1},'custom','曝光',1000);h.capture({exposure:.2},'custom','曝光',1100);
 assert.equal(h.states.length,2);assert.equal(h.undo().settings.exposure,0);
 assert.equal(h.redo().settings.exposure,.2);h.undo();h.capture({exposure:-.1},'custom','曝光',2000);
 assert.equal(h.states.length,2);assert.equal(h.redo().settings.exposure,-.1);
});
test('history snapshots restore cursor, preset and nested masks without sharing references',()=>{
 const p={exposure:0,masks:[{points:[[.2,.3]]}]};const h=new EditHistory(p,'natural');
 p.masks[0].points[0][0]=.9;assert.equal(h.states[0].settings.masks[0].points[0][0],.2);
 h.capture(p,'custom','蒙版',1000);h.undo();const saved=h.snapshot();
 const restored=new EditHistory({},'unused',saved);assert.equal(restored.index,0);
 assert.equal(restored.redo().preset,'custom');assert.equal(restored.states.length,2);
});
test('history memory is bounded and an unchanged preview adds no undo entry',()=>{
 const h=new EditHistory({x:0});for(let i=1;i<=100;i++)h.capture({x:i},'custom','调整',i*1000);
 assert.equal(h.states.length,50);assert.equal(h.capture({x:100},'custom'),false);
 assert.equal(h.states[h.index].settings.x,100);
});
test('browser snapshot compaction retains the active edit while trimming history',()=>{
 const h=new EditHistory({exposure:0});for(let i=1;i<30;i++)h.capture({exposure:i,text:'x'.repeat(100)},'custom','调整',i*1000);
 h.go(10);const doc={recipes:{one:{settings:h.states[10].settings,history:h.snapshot()}}};
 const saved=JSON.parse(boundedWorkspace(doc,600));assert.equal(saved.recipes.one.history.states[saved.recipes.one.history.index].settings.exposure,10);
 assert.equal(saved.recipes.one.settings.exposure,10);
});

test('discrete mask strokes and rotations remain separate; a local slider gesture coalesces',()=>{
 const h=new EditHistory({rotation:0,masks:[{type:'brush',exposure:0,points:[[.1,.1]]}]});
 h.capture({rotation:90,masks:h.states[0].settings.masks},'custom','旋转',1000);
 h.capture({rotation:180,masks:h.states[0].settings.masks},'custom','旋转',1100);
 const p=JSON.parse(JSON.stringify(h.states[h.index].settings));p.masks[0].points.push([.2,.2]);h.capture(p,'custom','画笔',1200);
 p.masks[0].points.push([.3,.3]);h.capture(p,'custom','画笔',1300);
 assert.equal(h.states.length,5);
 p.masks[0].exposure=.1;h.capture(p,'custom','局部曝光',1400);p.masks[0].exposure=.2;h.capture(p,'custom','局部曝光',1500);
 assert.equal(h.states.length,6);assert.equal(h.undo().settings.masks[0].exposure,0);
});

test('presets preserve manual optics, geometry and nested masks without sharing state',()=>{
 const source={exposure:1,contrast:15,saturation:12,wb:'daylight',rotation:1,crop:'1:1',denoise_luma:40,lens_k1:.005,masks:[{type:'brush',points:[[.1,.2]],breaks:[]}]};
 const defaults={exposure:0,contrast:0,highlights:0,shadows:0,temperature:0,tint:0,saturation:0,vibrance:0,sharpen:0};
 const out=applyGlobalPreset(source,defaults,{exposure:.15,saturation:-12});
 assert.equal(out.exposure,.15);assert.equal(out.contrast,0);assert.equal(out.saturation,-12);
 for(const key of ['wb','rotation','crop','denoise_luma','lens_k1','masks'])assert.deepEqual(out[key],source[key]);
 out.masks[0].points.push([.3,.4]);assert.equal(source.masks[0].points.length,1);
});
test('nested snapshots detect in-progress brush edits before a revision update and preserve AI undo',()=>{
 const {canApplyAuto}=require('../web/auto-grade.js');
 const photo={settings:{exposure:0,masks:[{points:[[.1,.2]],breaks:[]}]}};
 const base=cloneEdit(photo.settings),undo=cloneEdit(photo.settings),context={source:photo,base,revision:3};
 assert.equal(canApplyAuto(context,photo,[photo],3),true);
 photo.settings.masks[0].points.push([.3,.4]);
 assert.equal(canApplyAuto(context,photo,[photo],3),false);assert.equal(undo.masks[0].points.length,1);
 photo.settings=cloneEdit(undo);photo.settings.masks[0].points[0][0]=.9;assert.equal(undo.masks[0].points[0][0],.1);
});
