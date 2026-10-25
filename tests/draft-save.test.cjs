const {test}=require('node:test');
const assert=require('node:assert/strict');
const {DraftSaver}=require('../web/draft-save.js');
function fixture(save){let id=0,exposure=0,states=[];const saver=new DraftSaver({uuid:()=>String(++id).padStart(32,'0'),
 snapshot:()=>({ids:['photo'],recipes:{photo:{settings:{exposure}}}}),save,state:(...s)=>states.push(s)});
 return {saver,states,edit:v=>{exposure=v;saver.schedule();}};}
test('an uncertain disk response retries the exact snapshot and operation, then saves newer edits',async()=>{
 let calls=[],fail=true;const f=fixture(async data=>{calls.push(JSON.parse(JSON.stringify(data)));if(fail){fail=false;throw Error('connection lost');}return {revision:data.expected_version+1};});
 f.edit(.2);assert.equal(await f.saver.flush(),false);f.edit(.7);
 assert.equal(await f.saver.flush(),true);assert.deepEqual(calls[0],calls[1]);
 assert.equal(await f.saver.flush(),true);assert.equal(calls[2].recipes.photo.settings.exposure,.7);
 assert.equal(f.saver.version,2);assert.equal(f.saver.unsaved,false);
});
test('a stale tab cannot overwrite a newer draft or silently resubmit on further edits',async()=>{
 let calls=0;const f=fixture(async()=>{calls++;throw Object.assign(Error('conflict'),{status:409});});
 f.edit(.5);await f.saver.flush();f.edit(.8);await f.saver.flush();
 assert.equal(calls,1);assert.equal(f.saver.blocked,true);assert.equal(f.saver.unsaved,true);clearTimeout(f.saver.timer);
});
test('an edit during saving is captured in a later revision and is never acknowledged early',async()=>{
 let release;const f=fixture(()=>new Promise(r=>release=r));f.edit(.2);const saving=f.saver.flush();f.edit(.8);
 assert.equal(f.saver.unsaved,true);release({revision:1});await saving;assert.equal(f.saver.dirty,true);clearTimeout(f.saver.timer);
});
