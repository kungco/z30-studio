const {test}=require('node:test');const assert=require('node:assert/strict');
const {filterPhotos,LibraryWorkflow}=require('../web/library-workflow.js');
test('catalog filters combine rating flags filename and Unicode keywords',()=>{
 const photos=[{name:'Z30-1.NEF',catalog:{rating:4,flag:'pick',keywords:['人像','旅行']}},{name:'Z30-2.NEF',catalog:{rating:1,flag:'reject',keywords:['旅行']}},{name:'third.png'}];
 assert.deepEqual(filterPhotos(photos,{query:'人像 Z30',rating:3,flag:'pick'}),[photos[0]]);
 assert.equal(filterPhotos(photos,{query:'旅行'}).length,2);assert.equal(filterPhotos(photos,{rating:5}).length,0);
 assert.equal(filterPhotos(photos).length,3);
});
test('manual batch synchronization refuses a stale target without partial application',async()=>{
 let resolve;const a={id:'a',settings:{exposure:0}},b={id:'b',settings:{exposure:.2}};const photos=[a,b];let recorded=0;
 const control=Object.create(LibraryWorkflow.prototype);Object.assign(control,{busy:false,sync:()=>{},$ :()=>({querySelectorAll:()=>[{value:'light'}]}),io:{photos:()=>photos,validate:()=>new Promise(r=>resolve=r),record:()=>recorded++,applied:()=>assert.fail('must not apply'),error:()=>{}}});
 const pending=control.apply({exposure:1},photos);b.settings.exposure=.7;
 resolve({photos:[{id:'a',settings:{exposure:1}},{id:'b',settings:{exposure:1}}]});await pending;
 assert.equal(a.settings.exposure,0);assert.equal(b.settings.exposure,.7);assert.equal(recorded,0);assert.equal(control.busy,false);
});
