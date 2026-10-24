'use strict';
class BatchGrader {
 constructor(io){this.io=io;this.busy=false;this.active=null;this.cancelled=false;}
 async run(items,budget){
  if(this.busy)throw Error('批量调色正在进行');
  if(!Number.isInteger(budget)||budget<1||budget>8)throw Error('批量次数上限无效');
  this.busy=true;this.cancelled=false;const outcomes=[];let spent=0,reference='';
  try{
   for(let i=0;i<items.length;i++){
    const item=items[i];
    if(this.cancelled||spent>=budget){outcomes.push({state:'not-started'});continue;}
    if(!this.io.isCurrent(item)){outcomes.push({state:'stale'});this.io.onItem(i,outcomes[i]);continue;}
    item.autoOptions={...item.autoOptions,reference};
    this.active=this.io.make(item,i);spent++;
    const outcome=await this.active.start(item);outcomes.push(outcome);this.io.onItem(i,outcome);
    if(outcome.state==='done'&&!reference)reference=String(outcome.result.summary||'').slice(0,600);
    if(outcome.pending)this.cancelled=true;
   }
  }finally{while(outcomes.length<items.length)outcomes.push({state:'not-started'});this.active=null;this.busy=false;this.io.onFinish(outcomes,spent);}
  return outcomes;
 }
 async cancel(){this.cancelled=true;await this.active?.cancel();}
}
if(typeof module!=='undefined')module.exports={BatchGrader};
