'use strict';
const cloneEdit=value=>JSON.parse(JSON.stringify(value));
class EditHistory {
 constructor(settings,preset='custom',saved=null){
  this.limit=50;
  this.states=saved?.states?.length?cloneEdit(saved.states):[{settings:cloneEdit(settings),preset,label:'初始状态'}];
  this.index=Math.min(Math.max(saved?.index??0,0),this.states.length-1);
  this.lastKey=null;this.lastTime=0;
 }
 capture(settings,preset,label='调整参数',now=Date.now()){
  const previous=this.states[this.index];
  if(JSON.stringify(previous.settings)===JSON.stringify(settings)&&previous.preset===preset)return false;
  const changed=Object.keys(settings).filter(k=>JSON.stringify(previous.settings[k])!==JSON.stringify(settings[k]));
  let key=changed.length===1&&typeof settings[changed[0]]==='number'&&changed[0]!=='rotation'?changed[0]:null;
  if(changed.length===1&&changed[0]==='masks'&&Array.isArray(previous.settings.masks)&&previous.settings.masks.length===settings.masks.length){
   const indices=settings.masks.map((m,i)=>JSON.stringify(m)!==JSON.stringify(previous.settings.masks[i])?i:-1).filter(i=>i>=0);
   if(indices.length===1){const i=indices[0],m=settings.masks[i],before=previous.settings.masks[i];
    const fields=Object.keys(m).filter(k=>JSON.stringify(m[k])!==JSON.stringify(before[k]));
    if(fields.length===1&&['exposure','shadows','temperature','saturation','opacity','feather','radius'].includes(fields[0]))key='mask:'+i+':'+fields[0];
   }
  }
  const entry={settings:cloneEdit(settings),preset,label};
  if(this.index===this.states.length-1&&this.index>0&&key&&key===this.lastKey&&now-this.lastTime<450)this.states[this.index]=entry;
  else{this.states.splice(this.index+1);this.states.push(entry);if(this.states.length>this.limit)this.states.shift();this.index=this.states.length-1;}
  this.lastKey=key;this.lastTime=now;return true;
 }
 go(index){this.index=Math.max(0,Math.min(this.states.length-1,index));this.lastKey=null;return cloneEdit(this.states[this.index]);}
 undo(){return this.go(this.index-1);}
 redo(){return this.go(this.index+1);}
 snapshot(){return cloneEdit({states:this.states,index:this.index});}
}
function boundedWorkspace(document,maxChars=1800000){
 let text=JSON.stringify(document);
 while(text.length>maxChars){
  const largest=Object.values(document.recipes||{}).map(r=>r.history).filter(h=>h?.states?.length>1).sort((a,b)=>b.states.length-a.states.length)[0];
  if(!largest)throw Error('浏览器保存空间不足，请保存项目');
  if(largest.index<largest.states.length-1)largest.states.pop();else{largest.states.shift();largest.index--;}
  text=JSON.stringify(document);
 }
 return text;
}
if(typeof module!=='undefined')module.exports={EditHistory,boundedWorkspace};
