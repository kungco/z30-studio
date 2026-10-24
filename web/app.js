'use strict';
const $ = id => document.getElementById(id);
const defaults = {exposure:0,contrast:0,highlights:0,shadows:0,temperature:0,tint:0,saturation:0,vibrance:0,sharpen:0,rotation:0,crop:'original',wb:'camera'};
const presets = {neutral:{},portrait:{exposure:.15,contrast:-12,highlights:-22,shadows:12,temperature:7,vibrance:10},travel:{contrast:12,highlights:-20,shadows:15,vibrance:25,sharpen:18},film:{contrast:-9,highlights:-25,shadows:18,temperature:20,tint:6,saturation:-12},mono:{contrast:22,highlights:-15,shadows:8,saturation:-100,sharpen:20}};
const files = [];
let autoGrader=null,autoContext=null;
let current = null, importBusy = false, exportBusy = false, previewRunning = false, previewTimer, previewWanted = false, toastTimer, showingBefore = false, revision = 0;
const assistant = {connection:null, checking:false, running:false, source:null, base:null, result:null, image:null, strength:100, trialRevision:0, trialRunning:false, trialWanted:false, trialTimer:null, previewSignature:null, currentImage:null};
const sliderSpecs = [
 ['lightSliders','exposure','曝光',-4,4,.05,' EV'],
 ['lightSliders','contrast','对比度',-100,100,1,''],
 ['lightSliders','highlights','高光',-100,100,1,''],
 ['lightSliders','shadows','阴影',-100,100,1,''],
 ['colorSliders','temperature','色温',-100,100,1,''],
 ['colorSliders','tint','色调',-100,100,1,''],
 ['colorSliders','saturation','饱和度',-100,100,1,''],
 ['colorSliders','vibrance','自然饱和度',-100,100,1,''],
 ['detailSliders','sharpen','锐化',0,100,1,'']
];
sliderSpecs.forEach(([parent,id,label,min,max,step,suffix]) => {
 const row = document.createElement('div'); row.className = 'sliderrow';
 const l = document.createElement('label'); l.htmlFor = id; l.textContent = label;
 const out = document.createElement('output'); out.id = id+'Value'; out.htmlFor = id;
 l.append(out);
 const input = document.createElement('input'); Object.assign(input,{type:'range',id,min,max,step,value:0});
 input.setAttribute('aria-label',label);
 input.addEventListener('input',()=>{if(!current)return; current.settings[id]=Number(input.value);current.preset='custom';syncLabels();schedulePreview();});
 input.addEventListener('dblclick',()=>{if(!current)return;input.value=0;input.dispatchEvent(new Event('input'));});
 row.append(l,input); $(parent).append(row);
});

function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
function busy(show,message='正在更新预览…'){$('busy').hidden=!show;$('busyText').textContent=message;}
function setEnabled(){const yes=!!current,locked=exportBusy||importBusy; $('controls').disabled=!yes||locked;$('wb').disabled=!yes||!current.raw||locked; ['compare','rotate','zoom','reset','saveRecipe','loadRecipe'].forEach(id=>$(id).disabled=!yes||locked);$('exportTop').disabled=!yes||locked;document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=!yes||locked);syncAssistantControls();}
function syncLabels(){const p=current?current.settings:defaults;sliderSpecs.forEach(([,id,,,,step,suffix])=>{const n=p[id];$(id).value=n;$(id+'Value').textContent=(n>0?'+':'')+(step<1?n.toFixed(2):n)+suffix;});$('wb').value=p.wb;$('crop').value=p.crop;document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('selected',b.dataset.preset===(current?.preset||'neutral')));}
async function api(path,data,binary=false,extra={},options={}){const r=await fetch(path,{method:'POST',headers:{'Content-Type':binary?'application/octet-stream':'application/json','X-Z30-Request':'1',...extra},body:binary?data:JSON.stringify(data),...options});if(!r.ok){let message;try{message=(await r.json()).error;}catch{message=`处理失败 (${r.status})`;}throw Error(message);}return r;}
function histogram(hist){const c=$('histogram'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle='#2b2d2f';ctx.lineWidth=1;[.25,.5,.75].forEach(t=>{ctx.beginPath();ctx.moveTo(t*c.width,0);ctx.lineTo(t*c.width,c.height);ctx.stroke();});if(!hist)return;const peak=Math.max(1,...hist.flat());ctx.globalCompositeOperation='screen';['#b65e56','#599173','#5c83ae'].forEach((color,ch)=>{ctx.beginPath();ctx.moveTo(0,c.height);hist[ch].forEach((n,i)=>ctx.lineTo(i*c.width/127,c.height-(Math.log1p(n)/Math.log1p(peak))*c.height*.92));ctx.lineTo(c.width,c.height);ctx.closePath();ctx.fillStyle=color+'90';ctx.fill();});ctx.globalCompositeOperation='source-over';}
function drawLibrary(){$('fileList').replaceChildren();files.forEach(f=>{const card=document.createElement('div');card.className='filecard'+(current===f?' selected':'');const b=document.createElement('button');b.className='selectfile';b.title=f.name;const img=new Image();img.src=f.thumb;img.alt=f.name;const name=document.createElement('strong');name.textContent=f.name;const sub=document.createElement('small');sub.textContent=`${f.raw?'NEF RAW':'IMAGE'} · ${(f.bytes/1024/1024).toFixed(1)} MB`;b.append(img,name,sub);b.onclick=()=>selectFile(f);const remove=document.createElement('button');remove.className='remove';remove.textContent='×';remove.setAttribute('aria-label','移除 '+f.name);remove.onclick=()=>removeFile(f);card.append(b,remove);$('fileList').append(card);});$('count').textContent=String(files.length).padStart(2,'0');$('libraryHint').hidden=files.length>0;}
function selectFile(f){current=f;revision++;showingBefore=false;$('beforeLabel').hidden=true;$('empty').hidden=!!f;$('imageWrap').hidden=!f;setEnabled();syncLabels();drawLibrary();if(!f){$('filename').textContent='尚未选择照片';$('filetype').textContent='RAW WORKSPACE';$('imageMeta').textContent='为 NEF 照片而设计';$('renderState').textContent='准备就绪';histogram();return;}$('filename').textContent=f.name;$('filetype').textContent=f.raw?'NEF RAW':'IMAGE';$('imageMeta').textContent=`${f.camera} · ${f.width} × ${f.height} · sRGB`;$('preview').src=f.image;histogram(f.histogram);$('qualityStatus').textContent=f.quality?.message||'等待画质检查';schedulePreview(0);}
async function removeFile(f){if(exportBusy||importBusy)return;try{await api('/api/close',{id:f.id});files.splice(files.indexOf(f),1);if(current===f)selectFile(files[0]||null);else drawLibrary();}catch(e){toast(e.message);}}
async function importFiles(list){if(importBusy||exportBusy){toast('请等待当前处理完成');return;}const incoming=Array.from(list);if(!incoming.length)return;importBusy=true;setEnabled();let succeeded=0;const colorWarnings=[];for(const file of incoming){if(files.length>=8){toast('最多同时打开 8 张照片，请先移除照片');break;}if(!/\.(nef|jpg|jpeg|png)$/i.test(file.name)){toast('不支持 '+file.name+'，请选择 NEF、JPG 或 PNG');continue;}if(file.size>150*1024*1024){toast(file.name+' 超过 150 MB');continue;}busy(true,'正在解码 '+file.name+'…');try{const r=await api('/api/open',file,true,{'X-Filename':encodeURIComponent(file.name)});const d=await r.json();colorWarnings.push(...(d.warnings||[]));const f={...d,settings:{...defaults},preset:'neutral',thumb:d.image};files.push(f);selectFile(f);succeeded++;}catch(e){toast(file.name+'：'+e.message);}}importBusy=false;setEnabled();busy(false);if(succeeded)toast(`已导入 ${succeeded} 张照片，原文件保持不变`+(colorWarnings.length?"；"+[...new Set(colorWarnings)].join("；"):""));if(previewWanted)runPreview();$('fileInput').value='';}
function schedulePreview(delay=180){revision++;previewWanted=true;clearTimeout(previewTimer);previewTimer=setTimeout(runPreview,delay);$('renderState').textContent='等待更新…';}
async function runPreview(){if(previewRunning||importBusy||exportBusy||!current)return;previewRunning=true;previewWanted=false;const f=current,rev=revision;busy(true);const p={...f.settings};try{const d=await(await api('/api/preview',{id:f.id,settings:p,before:true})).json();if(rev===revision&&current===f){f.image=d.image;f.before=d.before;f.histogram=d.histogram;f.previewWidth=d.width;f.previewHeight=d.height;if(!showingBefore)$('preview').src=d.image;else $('preview').src=d.before;histogram(d.histogram);$('qualityStatus').textContent=d.quality?.message||'未发现明显的输出异常';$('renderState').textContent=`已更新 · 预览 ${d.width} × ${d.height}`;}}catch(e){toast(e.message);$('renderState').textContent='预览失败';}finally{previewRunning=false;if(!importBusy&&!exportBusy)busy(false);if(previewWanted)runPreview();}}
function compare(on){if(!current)return;showingBefore=on;$('preview').src=on?current.before:current.image;$('beforeLabel').hidden=!on;$('compare').classList.toggle('on',on);}
['importTop','importSide','importEmpty'].forEach(id=>$(id).onclick=()=>$('fileInput').click());
$('fileInput').onchange=e=>importFiles(e.target.files);
document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{if(!current)return;const geometry={rotation:current.settings.rotation,crop:current.settings.crop};current.settings={...defaults,...presets[b.dataset.preset],...geometry};current.preset=b.dataset.preset;syncLabels();schedulePreview(0);});
['wb','crop'].forEach(id=>$(id).onchange=()=>{if(!current)return;current.settings[id]=$(id).value;current.preset='custom';syncLabels();schedulePreview(0);});
$('rotate').onclick=()=>{current.settings.rotation=(current.settings.rotation+1)%4;schedulePreview(0);};
$('reset').onclick=()=>{if(!current)return;current.settings={...defaults};current.preset='neutral';syncLabels();schedulePreview(0);};
$('compare').onpointerdown=e=>{e.preventDefault();$('compare').setPointerCapture(e.pointerId);compare(true);};
$('compare').onpointerup=$('compare').onpointercancel=()=>compare(false);
$('compare').onclick=e=>{if(e.detail===0){compare(!showingBefore);}};
window.addEventListener('keydown',e=>{if(e.code==='Space'&&!/INPUT|SELECT|TEXTAREA|BUTTON/.test(document.activeElement.tagName)&&!$('exportDialog').open&&!$('assistantDialog').open){e.preventDefault();compare(true);}});
window.addEventListener('keyup',e=>{if(e.code==='Space')compare(false);});window.addEventListener('blur',()=>compare(false));
$('zoom').onclick=()=>{const on=$('imageWrap').classList.toggle('zoomed');$('zoom').textContent=on?'100%':'适应';};
let dragDepth=0;document.addEventListener('dragenter',e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();dragDepth++;$('dropOverlay').hidden=false;}});document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('dropOverlay').hidden=true;}});document.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('dropOverlay').hidden=true;importFiles(e.dataTransfer.files);});
function download(blob,name){const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),60000);}
$('saveRecipe').onclick=()=>{if(!current)return;download(new Blob([JSON.stringify({app:'Z30 Studio',version:1,source:current.name,settings:current.settings},null,2)],{type:'application/json'}),current.name.replace(/\.[^.]+$/,'')+'-z30.json');toast('参数已下载，可在下次导入照片后载入');};
$('loadRecipe').onclick=()=>$('recipeInput').click();
$('recipeInput').onchange=async e=>{try{if(!current||!e.target.files[0])return;const file=e.target.files[0];if(file.size>100000)throw Error('参数文件过大');const data=JSON.parse(await file.text());if(data.app!=='Z30 Studio'||data.version!==1||!data.settings)throw Error('这不是有效的 Z30 Studio 参数文件');const p={...defaults};sliderSpecs.forEach(([,id,,min,max])=>{const v=Number(data.settings[id]??0);if(!Number.isFinite(v))throw Error('参数包含无效数字');p[id]=Math.max(min,Math.min(max,v));});if(!['camera','auto','daylight'].includes(data.settings.wb)||!['original','3:2','4:3','1:1','16:9'].includes(data.settings.crop))throw Error('白平衡或裁剪参数无效');const rot=Number(data.settings.rotation||0);if(!Number.isInteger(rot)||rot<0||rot>3)throw Error('旋转参数无效');Object.assign(p,{wb:data.settings.wb,crop:data.settings.crop,rotation:rot});current.settings=p;current.preset='custom';syncLabels();schedulePreview(0);toast('参数已载入');}catch(err){toast(err.message);}finally{e.target.value='';}};
$('exportTop').onclick=()=>$('exportDialog').showModal();
$('quality').oninput=()=>$('qualityValue').textContent=$('quality').value;
$('exportFormat').onchange=()=>$('qualityRow').hidden=$('exportFormat').value!=='JPEG';
fetch('/api/health').then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{$('engine').textContent='LibRaw '+d.libraw+' · 就绪';}).catch(()=>{$('engine').textContent='解码服务连接失败';toast('请通过 start.bat 启动本地服务，再打开网页');});
function syncAssistantControls(){
 const locked=importBusy||exportBusy||!!autoGrader?.busy;
 $('autoGrade').disabled=!current||locked||assistant.running;
 $('autoCancel').hidden=!autoGrader?.busy;
 $('autoUndo').disabled=!current?.aiUndo||locked||assistant.running;
 $('autoDetails').disabled=!current?.autoResult||locked||assistant.running;
 if(current&&!assistant.running&&!assistant.result&&$('aiProgress').textContent.startsWith('请先导入'))$('aiProgress').textContent='描述目标后，发送预览生成建议。';
 $('aiGenerate').disabled=!current||locked||assistant.running||!assistant.connection?.ready;
 $('aiGoal').disabled=assistant.running||!!autoGrader?.busy;
 $('codexRecheck').disabled=assistant.running||assistant.checking;
 $('aiUndo').disabled=!current?.aiUndo||locked||assistant.running;
 document.querySelectorAll('[data-goal]').forEach(b=>b.disabled=assistant.running);
 const sameSource=assistant.source===current&&files.includes(assistant.source);
 const unchanged=sameSource&&assistant.base&&JSON.stringify(current.settings)===JSON.stringify(assistant.base);
 const candidate=assistant.result?assistantCandidate():null;
 const previewReady=candidate&&assistant.previewSignature===JSON.stringify(candidate);
 const alreadyApplied=sameSource&&current.autoResult?.result===assistant.result&&JSON.stringify(current.settings)===JSON.stringify(assistant.result.settings);
 $('aiApply').disabled=!unchanged||!assistant.result||locked||assistant.running||!previewReady;
 if(assistant.result){
  $('aiApplyHint').textContent=!sameSource?'建议属于另一张照片。请选择生成建议时的照片。':alreadyApplied?'自动调色已应用，可恢复应用前参数。':!unchanged?'当前参数已改变，请重新生成建议后再应用。':!previewReady?'正在生成本地试片，请稍候…':'应用会更新调色参数，可恢复应用前状态。';
 }
}
async function checkCodex(){
 if(assistant.checking)return;
 assistant.checking=true;$('codexStatus').textContent='正在检测本机 Codex…';syncAssistantControls();
 try{const r=await fetch('/api/codex/status');if(!r.ok)throw Error('连接检查失败');assistant.connection=await r.json();$('codexStatus').textContent=assistant.connection.message;}
 catch(e){assistant.connection={ready:false};$('codexStatus').textContent='本地服务连接失败，请检查启动窗口后重试';}
 finally{assistant.checking=false;syncAssistantControls();}
}
function assistantCandidate(){
 const t=assistant.strength/100,p={...assistant.base};
 if(t===0)return p;
 sliderSpecs.forEach(([,id,,min,max,step])=>{
  const value=assistant.base[id]+(assistant.result.settings[id]-assistant.base[id])*t;
  p[id]=Number(Math.max(min,Math.min(max,Math.round(value/step)*step)).toFixed(3));
 });
 return p;
}
function showAdvice(){
 $('aiEmpty').hidden=!!assistant.result;$('aiResult').hidden=!assistant.result;
 if(!assistant.result)return;
 const r=assistant.result;
 $('aiTitle').textContent=r.title;$('aiSummary').textContent=r.summary;$('aiLimitations').textContent=r.limitations+(r.quality?' '+r.quality.message:'');
 $('aiObservations').replaceChildren(...r.observations.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
 $('aiBeforeImage').src=assistant.currentImage;
 $('aiAfterImage').removeAttribute('src');
 $('aiStrength').value=assistant.strength;
 updateAdviceStrength();
}
function updateAdviceStrength(){
 if(!assistant.result)return;
 assistant.strength=Number($('aiStrength').value);$('aiStrengthValue').textContent=assistant.strength+'%';
 const p=assistantCandidate();
 $('aiChanges').replaceChildren(...sliderSpecs.filter(([,id])=>Math.abs(p[id]-assistant.base[id])>.001).map(([,id,label])=>{
  const row=document.createElement('div'),name=document.createElement('span'),value=document.createElement('span');name.textContent=label;
  value.textContent=`${assistant.base[id]} → ${Number(p[id].toFixed(2))}`;row.append(name,value);return row;
 }));
 assistant.trialRevision++;assistant.trialWanted=true;
 $('aiPreviewLabel').textContent='正在生成本地试片…';
 clearTimeout(assistant.trialTimer);assistant.trialTimer=setTimeout(runAssistantTrial,180);
 syncAssistantControls();
}
async function runAssistantTrial(){
 if(assistant.trialRunning||!assistant.result||!assistant.source)return;
 assistant.trialRunning=true;assistant.trialWanted=false;
 const rev=assistant.trialRevision,source=assistant.source,p=assistantCandidate();
 try{
  const d=await(await api('/api/codex/preview',{id:source.id,settings:p})).json();
  if(rev===assistant.trialRevision&&source===assistant.source){
   $('aiAfterImage').src=d.image;assistant.image=d.image;assistant.previewSignature=JSON.stringify(p);
   $('aiPreviewLabel').textContent='建议试片 · '+assistant.strength+'%';
  }
 }catch(e){$('aiPreviewLabel').textContent='试片失败';toast(e.message);}
 finally{assistant.trialRunning=false;syncAssistantControls();if(assistant.trialWanted)runAssistantTrial();}
}
async function generateAdvice(){
 if(!current||assistant.running||autoGrader?.busy||!assistant.connection?.ready)return;
 const source=current,base={...current.settings},goal=$('aiGoal').value.trim();
 if(!goal){toast('请先输入调色目标');return;}
 assistant.running=true;assistant.source=source;assistant.base=base;assistant.result=null;assistant.strength=100;assistant.previewSignature=null;
 assistant.trialRevision++;assistant.trialWanted=false;clearTimeout(assistant.trialTimer);
 $('aiResult').hidden=true;$('aiEmpty').hidden=false;$('aiProgress').textContent='正在准备预览并发送给 Codex…';syncAssistantControls();
 try{
  const d=await(await api('/api/codex/suggest',{id:source.id,settings:base,goal})).json();
  assistant.currentImage=d.current_image;
  const start=Date.now();let job;
  while(true){
   if(Date.now()-start>210000)throw Error('等待建议超时，请稍后重新检测 Codex 后再试');
   await new Promise(resolve=>setTimeout(resolve,1100));
   const response=await fetch('/api/codex/jobs/'+encodeURIComponent(d.job));
   if(!response.ok){const error=await response.json();throw Error(error.error||'无法读取建议任务，请重新生成');}job=await response.json();
   if(job.state==='done')break;
   if(job.state==='error'||job.state==='cancelled')throw Error(job.error||'照片会话已关闭');
   $('aiProgress').textContent=`Codex 正在看图与分析… ${Math.round((Date.now()-start)/1000)} 秒。关闭面板后任务仍会继续。`;
  }
  if(job.source_id!==source.id)throw Error('建议与当前照片不匹配，请重新生成');
  assistant.result=job.result;showAdvice();
  $('aiProgress').textContent='建议已就绪。调整强度、查看试片，再决定是否应用。';
  if(!$('assistantDialog').open)toast('Codex 建议已就绪，点击“Codex 辅助”查看');
 }catch(e){$('aiProgress').textContent=e.message;toast(e.message);}
 finally{assistant.running=false;syncAssistantControls();}
}
$('assistantTop').onclick=()=>{
 $('assistantDialog').showModal();syncAssistantControls();
 if(!current&&!assistant.running)$('aiProgress').textContent='请先导入一张照片，再让 Codex 给出建议。';
 else if(!assistant.result&&!assistant.running)$('aiProgress').textContent='描述目标后，发送预览生成建议。';
 if(!assistant.connection)checkCodex();
};
$('assistantClose').onclick=()=>$('assistantDialog').close();
$('codexRecheck').onclick=checkCodex;
$('aiGenerate').onclick=generateAdvice;
$('aiStrength').oninput=updateAdviceStrength;
document.querySelectorAll('[data-goal]').forEach(b=>b.onclick=()=>{if(!assistant.running)$('aiGoal').value=b.dataset.goal;});
$('aiApply').onclick=()=>{
 if($('aiApply').disabled||assistant.source!==current)return;
 current.aiUndo={settings:{...current.settings},preset:current.preset};
 current.settings=assistantCandidate();current.preset='custom';syncLabels();schedulePreview(0);syncAssistantControls();
 $('assistantDialog').close();toast('已应用 Codex 建议，可在辅助面板恢复应用前参数');
};
function undoAI(){
 if(!current?.aiUndo||importBusy||exportBusy||autoGrader?.busy||assistant.running)return;
 current.settings={...current.aiUndo.settings};current.preset=current.aiUndo.preset;delete current.aiUndo;
 syncLabels();schedulePreview(0);syncAssistantControls();toast('已恢复应用前参数');
 $('autoStatus').textContent='已恢复上次 AI 调色前的参数。';
}
$('aiUndo').onclick=undoAI;
$('autoUndo').onclick=undoAI;
setEnabled();syncLabels();histogram();

const jobLabels={queued:'排队中',running:'正在导出',done:'已完成',partial:'部分完成',error:'失败，可重试',cancelled:'已取消',interrupted:'上次中断，可重试'};
let jobsTimer=null,jobsLoading=false;
async function jobAction(action,id){
 try{await api('/api/exports/'+action,{job:id});await refreshExportJobs();}
 catch(e){toast(e.message);}
}
async function refreshExportJobs(){
 if(jobsLoading)return;
 jobsLoading=true;clearTimeout(jobsTimer);
 try{
  const response=await fetch('/api/exports');const data=await response.json();if(!response.ok)throw Error(data.error||'无法读取后台任务');
  const active=data.jobs.filter(j=>j.state==='running'||j.state==='queued'||j.worker_active).length;
  $('jobsTop').textContent=active?'后台任务 · '+active:'后台任务';
  $('jobsStorage').textContent=`本机快照与结果 ${(data.storage_bytes/1048576).toFixed(1)} MiB / ${(data.limit_bytes/1048576).toFixed(0)} MiB；${data.jobs.length} 个任务`;
  $('jobsList').replaceChildren();
  if(!data.jobs.length){const empty=document.createElement('p');empty.textContent='暂无后台导出任务。';$('jobsList').append(empty);}
  for(const job of data.jobs){
   const card=document.createElement('section');card.className='export-job';
   const count=job.items.filter(x=>x.state==='done').length;
   const title=document.createElement('h3');title.textContent=`${jobLabels[job.state]||job.state} · ${count}/${job.items.length} 张 · ${job.format}`;
   const progress=document.createElement('progress');progress.max=job.items.length;progress.value=count;
   const list=document.createElement('ul');
   for(const item of job.items){const li=document.createElement('li');li.textContent=item.name+' · '+(item.state==='done'?'完成':item.state==='running'?'正在处理':item.state==='error'?'失败：'+item.error:'等待处理')+(item.quality?.strength<1?' · 自动保护 '+Math.round(item.quality.strength*100)+'%':'');list.append(li);}
   const actions=document.createElement('div');actions.className='job-actions';
   if(count){
    const link=document.createElement('a');const single=job.items.length===1;
    link.href='/api/exports/'+encodeURIComponent(job.id)+(single?'/file':'/download');link.textContent=single?'下载照片':'下载已完成结果 ZIP';link.setAttribute('download','');actions.append(link);
    if(!single)for(const item of job.items.filter(x=>x.state==='done')){
     const file=document.createElement('a');file.href='/api/exports/'+encodeURIComponent(job.id)+'/files/'+item.index;
     file.textContent='单张下载：'+item.name;file.setAttribute('download','');actions.append(file);
    }
   }
   if(['partial','error','cancelled','interrupted'].includes(job.state)&&!job.worker_active){const retry=document.createElement('button');retry.textContent='重试未完成项';retry.onclick=()=>jobAction('retry',job.id);actions.append(retry);}
   if(job.state==='running'||job.state==='queued'){const cancel=document.createElement('button');cancel.textContent='取消任务';cancel.onclick=()=>jobAction('cancel',job.id);actions.append(cancel);}
   else if(!job.worker_active){const remove=document.createElement('button');remove.textContent='删除快照与结果';remove.onclick=()=>jobAction('delete',job.id);actions.append(remove);}
   card.append(title,progress,list);
   if(job.error){const error=document.createElement('p');error.textContent=job.error;card.append(error);}
   card.append(actions);$('jobsList').append(card);
  }
  if(active||$('jobsDialog').open)jobsTimer=setTimeout(refreshExportJobs,1500);
 }catch(e){$('jobsStorage').textContent=e.message;if($('jobsDialog').open)jobsTimer=setTimeout(refreshExportJobs,5000);}
 finally{jobsLoading=false;}
}
$('jobsTop').onclick=()=>{$('jobsDialog').showModal();refreshExportJobs();};
$('jobsClose').onclick=()=>$('jobsDialog').close();
$('exportConfirm').onclick=async()=>{
 if(!current||exportBusy)return;
 const sources=$('exportScope').value==='all'?[...files]:[current];
 const body={ids:sources.map(f=>f.id),settings:{...current.settings},format:$('exportFormat').value,
             quality:Number($('quality').value),protect:$('exportProtect').checked};
 exportBusy=true;setEnabled();$('exportConfirm').disabled=true;
 try{
  await api('/api/exports/create',body);$('exportDialog').close();$('jobsDialog').showModal();
  toast('已创建本机快照并加入后台队列，可继续编辑照片');await refreshExportJobs();
 }catch(e){toast(e.message);}
 finally{exportBusy=false;setEnabled();$('exportConfirm').disabled=false;if(previewWanted)runPreview();}
};
refreshExportJobs();

const autoPost=(path,data)=>api(path,data,false,{}, {signal:AbortSignal.timeout(25000)});
autoGrader=new AutoGrader({
 submit:async context=>{
  assistant.trialRevision++;assistant.trialWanted=false;clearTimeout(assistant.trialTimer);
  const data=await(await autoPost('/api/codex/auto',{id:context.source.id,settings:context.base})).json();
  context.currentImage=data.current_image;return data;
 },
 poll:async job=>{
  const response=await fetch('/api/codex/jobs/'+encodeURIComponent(job),{signal:AbortSignal.timeout(15000)});
  const data=await response.json();if(!response.ok)throw Error(data.error||'无法读取自动调色任务');return data;
 },
 cancel:(context,job)=>autoPost('/api/codex/cancel',{id:context.source.id,job}),
 preview:async(context,settings)=>{
  const data=await(await autoPost('/api/preview',{id:context.source.id,settings,before:true})).json();
  const image=new Image();image.src=data.image;await image.decode();return data;
 },
 isCurrent:context=>canApplyAuto(context,current,files,revision),
 apply:(context,result,preview)=>{
  const f=context.source;
  f.aiUndo={settings:{...f.settings},preset:f.preset};
  f.settings={...result.settings};f.preset='custom';
  // Invalidate a pending old preview before publishing the verified new image.
  revision++;clearTimeout(previewTimer);previewWanted=false;showingBefore=false;
  Object.assign(f,{image:preview.image,before:preview.before,histogram:preview.histogram,quality:preview.quality});
  $('beforeLabel').hidden=true;$('preview').src=f.image;histogram(f.histogram);
  $('qualityStatus').textContent=preview.quality.message;
  $('renderState').textContent=`GPT 自动调色已应用 · 预览 ${preview.width} × ${preview.height}`;
  syncLabels();
 },
 onState:state=>{
  const labels={preparing:'正在准备预览…',analyzing:'GPT 正在分析画面与调色…',checking:'正在检查通道、层次和色彩…',rendering:'正在验证本地调色预览…',cancelling:'正在取消；不会自动应用结果。'};
  const context=state.context||autoContext;
  if(state.result&&files.includes(context.source))context.source.autoResult={base:context.base,result:state.result,currentImage:context.currentImage};
  let message=labels[state.state]||state.message;
  if(state.state==='done')message='已自动应用。'+state.result.summary+' '+state.result.quality.message;
  if(state.state==='stale')message='照片或参数已改变，未自动应用。可回到原照片查看分析。';
  $('autoStatus').textContent=(context?context.source.name+' · ':'')+(message||'自动调色未完成');
  $('autoGrade').textContent=autoGrader?.busy?'正在自动调色…':'一键 GPT 调色';
  syncAssistantControls();
  if(['done','stale','error','cancelled'].includes(state.state))toast(message);
 }
});
$('autoGrade').onclick=()=>{
 if(!current||autoGrader.busy||assistant.running||importBusy||exportBusy)return;
 autoContext={source:current,base:{...current.settings},revision};
 autoGrader.start(autoContext).catch(e=>toast(e.message));
};
$('autoCancel').onclick=()=>autoGrader.cancel();
$('autoDetails').onclick=()=>{
 if(!current?.autoResult||autoGrader.busy||assistant.running)return;
 const saved=current.autoResult;
 Object.assign(assistant,{source:current,base:saved.base,result:saved.result,currentImage:saved.currentImage,strength:100,previewSignature:null});
 $('aiProgress').textContent='这是自动调色的分析与画质检查记录。';showAdvice();
 $('assistantDialog').showModal();syncAssistantControls();
 if(!assistant.connection)checkCodex();
};
setEnabled();
