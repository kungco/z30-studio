'use strict';
// Local writes only. Retain the exact operation ID and snapshot on an uncertain
// response; retries cannot make duplicate versions or overwrite another tab.
class DraftSaver {
 constructor(io){this.io=io;this.dirty=false;this.running=false;this.pending=null;this.timer=null;this.firstDirty=0;this.version=0;this.id=null;this.blocked=false;}
 reset(id=null,version=0){clearTimeout(this.timer);this.id=id;this.version=version;this.pending=null;this.dirty=false;this.blocked=false;this.firstDirty=0;}
 schedule(){this.dirty=true;if(!this.firstDirty)this.firstDirty=Date.now();clearTimeout(this.timer);if(!this.blocked)this.timer=setTimeout(()=>this.flush(),Math.max(0,Math.min(1000,5000-(Date.now()-this.firstDirty))));this.io.state?.('pending');}
 async flush(snapshot=false){
  clearTimeout(this.timer);if(this.running||this.blocked||(!this.dirty&&!snapshot&&!this.pending))return false;
  this.running=true;this.io.state?.('saving');
  try{
   if(!this.pending){
    const data=this.io.snapshot();
    if(!data.ids.length&&!this.id){this.dirty=false;this.firstDirty=0;return true;}
    this.id??=this.io.uuid();
    this.pending={...data,draft_id:this.id,expected_version:this.version,request_id:this.io.uuid(),snapshot};
    this.dirty=false;this.firstDirty=0;
   }
   const result=await this.io.save(this.pending);
   this.version=result.revision;this.pending=null;
   this.io.ack?.({id:this.id,version:this.version});this.io.state?.('saved',result);
   return true;
  }catch(error){
   if(error.status===409)this.blocked=true;
   this.io.state?.('error',error);return false;
  }finally{this.running=false;if(this.dirty&&!this.pending&&!this.blocked)this.schedule();}
 }
 get unsaved(){return this.dirty||!!this.pending||this.running;}
}
if(typeof module!=='undefined')module.exports={DraftSaver};
