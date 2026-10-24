'use strict';

function canApplyAuto(context,current,files,revision){
 return current===context.source&&files.includes(context.source)&&revision===context.revision&&
  JSON.stringify(current.settings)===JSON.stringify(context.base);
}

// A run owns its source/settings/revision snapshot. Only a verified local render
// may commit, synchronously, while that snapshot is still current.
class AutoGrader {
 constructor(options){
  this.io=options;this.active=null;
  this.sleep=options.sleep||((ms)=>new Promise(resolve=>setTimeout(resolve,ms)));
  this.now=options.now||Date.now;
 }
 get busy(){return this.active!==null;}
 emit(state,extra={}){this.io.onState({state,...extra});}
 async stopRemote(run){
  if(run.job&&!run.cancelPromise){
   run.cancelPromise=Promise.resolve().then(()=>this.io.cancel(run.context,run.job)).catch(()=>{
    run.cancelError='已停止自动应用；未能确认服务端取消，请检查本地服务。';
   });
  }
  await run.cancelPromise;
 }
 async cancel(){
  const run=this.active;if(!run)return;
  run.cancelled=true;this.emit('cancelling');await this.stopRemote(run);
 }
 async start(context){
  if(this.busy)throw Error('自动调色正在进行');
  const run={context,job:null,cancelled:false};this.active=run;
  let outcome;
  this.emit('preparing');
  try{
   const response=await this.io.submit(context);run.job=response.job;
   const start=this.now();let job;
   while(!run.cancelled){
    if(this.now()-start>(this.io.timeoutMs??210000)){
     run.cancelled=true;await this.stopRemote(run);
     throw Error('自动调色超时；已停止自动应用，请稍后手动重试。');
    }
    this.emit('analyzing');await this.sleep(1100);
    if(run.cancelled)break;
    job=await this.io.poll(run.job);
    if(job.state==='done')break;
    if(job.state==='error'||job.state==='cancelled')throw Error(job.error||'分析已取消');
    if(job.phase==='checking')this.emit('checking');
   }
   if(run.cancelled){
    await this.stopRemote(run);outcome={state:'cancelled',message:run.cancelError||'已取消，当前调色参数未改变。'};
   }else{
    if(job.source_id!==context.source.id)throw Error('分析结果与照片不匹配，未应用。');
    const result=job.result;
    if(!result?.settings||!Number.isFinite(result.quality?.strength)||result.quality.strength<0||result.quality.strength>1)
     throw Error('分析缺少有效参数或画质检查，未应用。');
    if(['wb','crop','rotation'].some(key=>result.settings[key]!==context.base[key])||
       Object.keys(context.base).some(key=>typeof context.base[key]==='number'&&!Number.isFinite(result.settings[key])))
     throw Error('调色参数或构图不匹配，未应用。');
    if(!this.io.isCurrent(context))outcome={state:'stale',context,result};
    else{
     this.emit('rendering');
     const preview=await this.io.preview(context,result.settings);
     if(!preview?.image||!preview.before||!preview.quality||!preview.histogram||!(preview.width>0&&preview.height>0))
      throw Error('本地调色预览无效，未应用。');
     if(run.cancelled){
      await this.stopRemote(run);outcome={state:'cancelled',message:run.cancelError||'已取消，当前调色参数未改变。'};
     }else if(!this.io.isCurrent(context))outcome={state:'stale',context,result};
     else{
      // isCurrent and apply must share this synchronous turn: no late overwrite.
      this.io.apply(context,result,preview);outcome={state:'done',context,result};
     }
    }
   }
  }catch(error){outcome={state:run.cancelled?'cancelled':'error',message:run.cancelError||error.message};}
  finally{this.active=null;this.io.onState(outcome||{state:'error',message:'自动调色未完成'});}
  return outcome;
 }
}

if(typeof module!=='undefined')module.exports={AutoGrader,canApplyAuto};
