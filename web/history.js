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
  const key=changed.length===1?changed[0]:null;
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
if(typeof module!=='undefined')module.exports={EditHistory};
