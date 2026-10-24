const {test}=require('node:test');
const assert=require('node:assert/strict');
const {AutoGrader,canApplyAuto}=require('../web/auto-grade.js');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('production edit guard rejects removed photos, switched photos, changed settings and ABA edits',()=>{
 const photo={id:'one',settings:{exposure:0}};
 const context={source:photo,base:{exposure:0},revision:3};
 assert.equal(canApplyAuto(context,photo,[photo],3),true);
 assert.equal(canApplyAuto(context,photo,[],3),false);
 assert.equal(canApplyAuto(context,{id:'two'},[photo],3),false);
 photo.settings.exposure=.5;assert.equal(canApplyAuto(context,photo,[photo],3),false);
 photo.settings.exposure=0;assert.equal(canApplyAuto(context,photo,[photo],5),false);
});
function setup(overrides={}){
 const context={source:{id:'photo'},base:{exposure:0,wb:'camera',crop:'original',rotation:0},revision:3};
 const result={settings:{...context.base,exposure:.5},quality:{strength:.75}};
 const job={state:'done',source_id:'photo',result};
 const preview={image:'local-render',before:'baseline',width:12,height:8,histogram:[],quality:{}};
 const states=[],applied=[],cancelled=[];let submissions=0;
 const grader=new AutoGrader({
  submit:async()=>{submissions++;return {job:'job'};},poll:async()=>job,
  preview:async()=>preview,apply:(...args)=>applied.push(args),isCurrent:()=>true,
  cancel:async(...args)=>cancelled.push(args),onState:s=>states.push(s),sleep:async()=>{},...overrides
 });
 return {grader,context,result,job,preview,states,applied,cancelled,submissions:()=>submissions};
}
test('one click renders checked settings, then commits once without another model call',async()=>{
 const s=setup();assert.equal((await s.grader.start(s.context)).state,'done');
 assert.equal(s.submissions(),1);assert.equal(s.applied.length,1);
 assert.equal(s.applied[0][1],s.result);assert.equal(s.applied[0][2],s.preview);
 assert.equal(s.grader.busy,false);
});
test('double clicks cannot start a second model call',async()=>{
 const pending=deferred();const s=setup({poll:()=>pending.promise});
 const first=s.grader.start(s.context);await tick();
 await assert.rejects(s.grader.start(s.context),/正在进行/);
 pending.resolve(s.job);await first;assert.equal(s.submissions(),1);
});
test('changing source or editing then restoring parameters prevents automatic commit',async()=>{
 for(const change of ['photo','revision']){
  const pending=deferred();let current=true;
  const s=setup({poll:()=>pending.promise,isCurrent:()=>current});
  const work=s.grader.start(s.context);await tick();current=false;pending.resolve(s.job);
  assert.equal((await work).state,'stale',change);assert.equal(s.applied.length,0);
 }
});
test('edits during local rendering also prevent the commit',async()=>{
 const pending=deferred();let current=true;
 const s=setup({preview:()=>pending.promise,isCurrent:()=>current});
 const work=s.grader.start(s.context);await tick();current=false;pending.resolve(s.preview);
 assert.equal((await work).state,'stale');assert.equal(s.applied.length,0);
});
test('cancel before job id arrives still cancels it once and never applies',async()=>{
 const pending=deferred();const s=setup({submit:()=>pending.promise});
 const work=s.grader.start(s.context);await s.grader.cancel();pending.resolve({job:'late-job'});
 assert.equal((await work).state,'cancelled');assert.equal(s.cancelled.length,1);
 assert.equal(s.cancelled[0][1],'late-job');assert.equal(s.applied.length,0);
});
test('cancel after a done response while rendering prevents late application',async()=>{
 const pending=deferred();const s=setup({preview:()=>pending.promise});
 const work=s.grader.start(s.context);await tick();await s.grader.cancel();pending.resolve(s.preview);
 assert.equal((await work).state,'cancelled');assert.equal(s.applied.length,0);
});
test('server error or bad preview leaves existing settings intact without auto retry',async()=>{
 for(const overrides of [{poll:async()=>{throw Error('额度不足');}},
  {preview:async()=>{throw Error('显影失败');}},{preview:async()=>({})}]){
  const s=setup(overrides);const original={...s.context.base};
  assert.equal((await s.grader.start(s.context)).state,'error');
  assert.equal(s.submissions(),1);assert.equal(s.applied.length,0);assert.deepEqual(s.context.base,original);
 }
});
test('source mismatch, missing quality check and changed composition are rejected',async()=>{
 for(const mutation of [s=>s.job.source_id='other',s=>delete s.result.quality,
  s=>s.result.settings.crop='1:1',s=>s.result.settings.exposure=NaN]){
  const s=setup();mutation(s);assert.equal((await s.grader.start(s.context)).state,'error');
  assert.equal(s.applied.length,0);
 }
});
test('timeout cancels remote task and never retries',async()=>{
 let clock=0;const s=setup({poll:async()=>({state:'running'}),now:()=>clock,sleep:async()=>{clock+=10;},timeoutMs:15});
 const outcome=await s.grader.start(s.context);assert.equal(outcome.state,'cancelled');
 assert.match(outcome.message,/超时/);assert.equal(s.cancelled.length,1);assert.equal(s.submissions(),1);
});
test('failed cancellation is reported without permitting a late application',async()=>{
 const pending=deferred();const s=setup({poll:()=>pending.promise,cancel:async()=>{throw Error('offline');}});
 const work=s.grader.start(s.context);await tick();await s.grader.cancel();pending.resolve(s.job);
 const outcome=await work;assert.equal(outcome.state,'cancelled');assert.match(outcome.message,/未能确认/);
 assert.equal(s.applied.length,0);
});
