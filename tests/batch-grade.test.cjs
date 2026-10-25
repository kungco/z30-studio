const {test}=require('node:test');const assert=require('node:assert/strict');const {BatchGrader}=require('../web/batch-grade.js');
test('batch grades sequentially with a shared style reference and an explicit request budget',async()=>{
 let running=0,max=0,calls=0;const items=[{},{},{}];
 const b=new BatchGrader({isCurrent:()=>true,make:()=>({start:async()=>{calls++;running++;max=Math.max(max,running);await Promise.resolve();running--;return {state:'done',result:{summary:'warm subtle'}};}}),onItem:()=>{},onFinish:()=>{}});
 const results=await b.run(items,2);assert.equal(calls,2);assert.equal(max,1);assert.equal(results[2].state,'not-started');assert.equal(items[1].autoOptions.reference,'warm subtle');
});
test('edits to a queued photo skip it and an uncertain network result stops later analysis',async()=>{
 let calls=0;const b=new BatchGrader({isCurrent:x=>!x.changed,make:()=>({start:async()=>{calls++;return {state:'error',pending:true};}}),onItem:()=>{},onFinish:()=>{}});
 const results=await b.run([{changed:true},{},{}],3);assert.equal(calls,1);assert.equal(results[0].state,'stale');assert.equal(results[2].state,'not-started');
});
test('stopping an active batch cancels the current item and starts no later photo',async()=>{
 let release;let calls=0;const wait=new Promise(resolve=>release=resolve);
 const b=new BatchGrader({isCurrent:()=>true,make:()=>({start:async()=>{calls++;await wait;return {state:'cancelled'};},cancel:async()=>release()}),onItem:()=>{},onFinish:()=>{}});
 const task=b.run([{},{}],2);await new Promise(resolve=>setImmediate(resolve));await b.cancel();
 const results=await task;assert.equal(calls,1);assert.equal(results[1].state,'not-started');assert.equal(b.busy,false);
});

test('larger libraries still require an explicit bounded analysis budget',async()=>{
 let calls=0;const grader=new BatchGrader({isCurrent:()=>true,make:()=>({start:async()=>{calls++;return {state:'done',result:{summary:'style'}};}}),onItem:()=>{},onFinish:()=>{}});
 await grader.run(Array.from({length:12},()=>({autoOptions:{}})),9);assert.equal(calls,9);
 await assert.rejects(()=>grader.run([],257));
});
