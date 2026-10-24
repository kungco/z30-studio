const {test}=require('node:test');const assert=require('node:assert/strict');
const {EditHistory}=require('../web/history.js');
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
