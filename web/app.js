'use strict';
const $ = id => document.getElementById(id);
let aiProvider=null;
let clientId;
try{clientId=sessionStorage.getItem('z30-client');if(!/^[a-f0-9]{32}$/.test(clientId||'')){clientId=crypto.randomUUID().replaceAll('-','');sessionStorage.setItem('z30-client',clientId);}}
catch{clientId=crypto.randomUUID().replaceAll('-','');}
const localFetch=(path,options={})=>fetch(path,{...options,headers:{'X-Z30-Client':clientId,...options.headers}});
function savedWorkspace(){try{return JSON.parse(sessionStorage.getItem('z30-workspace')||'{}');}catch{return {};}}
function persistWorkspace(){
 try{
  const recipes={};for(const f of files)recipes[f.id]={settings:f.settings,preset:f.preset,undo:f.aiUndo,history:f.history?.snapshot(),
   pending:f.autoPending?{base:f.autoPending.base,requestId:f.autoPending.requestId}:null};
  sessionStorage.setItem('z30-workspace',boundedWorkspace({selected:current?.id,recipes}));
 }catch{/* Manual recipe download remains available if browser storage is disabled. */}
}

const defaults = {exposure:0,contrast:0,highlights:0,shadows:0,temperature:0,tint:0,saturation:0,vibrance:0,sharpen:0,rotation:0,crop:'original',wb:'camera',denoise_luma:0,denoise_chroma:0,lens_k1:0,lens_k2:0,lens_red:0,lens_blue:0,vignette:0,lens_profile:'手动 / 未校准',masks:[]};
const presets = {neutral:{},portrait:{exposure:.15,contrast:-12,highlights:-22,shadows:12,temperature:7,vibrance:10},travel:{contrast:12,highlights:-20,shadows:15,vibrance:25,sharpen:18},film:{contrast:-9,highlights:-25,shadows:18,temperature:20,tint:6,saturation:-12},mono:{contrast:22,highlights:-15,shadows:8,saturation:-100,sharpen:20}};
const files = [];
let autoGrader=null,autoContext=null,batchGrader=null;
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
 ['detailSliders','sharpen','锐化',0,100,1,''],
 ['advancedSliders','denoise_luma','亮度降噪',0,100,1,''],
 ['advancedSliders','denoise_chroma','色彩降噪',0,100,1,''],
 ['advancedSliders','lens_k1','径向畸变 k1',-.3,.3,.005,''],
 ['advancedSliders','lens_k2','径向畸变 k2',-.2,.2,.005,''],
 ['advancedSliders','lens_red','红色差径向',-10,10,.1,' ‰'],
 ['advancedSliders','lens_blue','蓝色差径向',-10,10,.1,' ‰'],
 ['advancedSliders','vignette','暗角补偿',0,100,1,'']
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
function setEnabled(){const yes=!!current,locked=exportBusy||importBusy; $('controls').disabled=!yes||locked;$('wb').disabled=!yes||!current.raw||locked; ['compare','rotate','zoom','reset','saveRecipe','loadRecipe'].forEach(id=>$(id).disabled=!yes||locked);$('exportTop').disabled=!yes||locked;document.querySelectorAll('.advanced-panel input,.advanced-panel button,.advanced-panel select').forEach(el=>el.disabled=!yes||locked);document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=!yes||locked);syncAssistantControls();syncMaskUI();syncHistory();}
function syncLabels(){syncHistory();const p=current?current.settings:defaults;sliderSpecs.forEach(([,id,,,,step,suffix])=>{const n=p[id];$(id).value=n;$(id+'Value').textContent=(n>0?'+':'')+(step<1?n.toFixed(step<.01?3:2):n)+suffix;});$('wb').value=p.wb;$('crop').value=p.crop;$('lensProfileName').value=p.lens_profile||'手动 / 未校准';syncMaskUI();document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('selected',b.dataset.preset===(current?.preset||'neutral')));}
async function api(path,data,binary=false,extra={},options={}){if(['/api/codex/suggest','/api/codex/auto'].includes(path))data={...data,backend_revision:data.backend_revision??aiProvider?.revision};const r=await localFetch(path,{method:'POST',headers:{'Content-Type':binary?'application/octet-stream':'application/json','X-Z30-Request':'1',...extra},body:binary?data:JSON.stringify(data),...options});if(!r.ok){let message;try{message=(await r.json()).error;}catch{message=`处理失败 (${r.status})`;}throw Object.assign(Error(message),{status:r.status,retryable:r.status>=500||r.status===408||r.status===429});}return r;}
function histogram(hist){const c=$('histogram'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle='#2b2d2f';ctx.lineWidth=1;[.25,.5,.75].forEach(t=>{ctx.beginPath();ctx.moveTo(t*c.width,0);ctx.lineTo(t*c.width,c.height);ctx.stroke();});if(!hist)return;const peak=Math.max(1,...hist.flat());ctx.globalCompositeOperation='screen';['#b65e56','#599173','#5c83ae'].forEach((color,ch)=>{ctx.beginPath();ctx.moveTo(0,c.height);hist[ch].forEach((n,i)=>ctx.lineTo(i*c.width/127,c.height-(Math.log1p(n)/Math.log1p(peak))*c.height*.92));ctx.lineTo(c.width,c.height);ctx.closePath();ctx.fillStyle=color+'90';ctx.fill();});ctx.globalCompositeOperation='source-over';}
function drawLibrary(){$('fileList').replaceChildren();files.forEach(f=>{const card=document.createElement('div');card.className='filecard'+(current===f?' selected':'');const b=document.createElement('button');b.className='selectfile';b.title=f.name;const img=new Image();img.src=f.thumb;img.alt=f.name;const name=document.createElement('strong');name.textContent=f.name;const sub=document.createElement('small');sub.textContent=`${f.raw?'NEF RAW':'IMAGE'} · ${(f.bytes/1024/1024).toFixed(1)} MB`;b.append(img,name,sub);b.onclick=()=>selectFile(f);const remove=document.createElement('button');remove.className='remove';remove.textContent='×';remove.setAttribute('aria-label','移除 '+f.name);remove.onclick=()=>removeFile(f);card.append(b,remove);$('fileList').append(card);});$('count').textContent=String(files.length).padStart(2,'0');$('libraryHint').hidden=files.length>0;}
function selectFile(f){current=f;revision++;showingBefore=false;$('beforeLabel').hidden=true;$('empty').hidden=!!f;$('imageWrap').hidden=!f;setEnabled();syncLabels();drawLibrary();if(!f){$('filename').textContent='尚未选择照片';$('filetype').textContent='RAW WORKSPACE';$('imageMeta').textContent='为 NEF 照片而设计';$('renderState').textContent='准备就绪';histogram();return;}$('filename').textContent=f.name;$('filetype').textContent=f.raw?'NEF RAW':'IMAGE';$('imageMeta').textContent=`${f.camera} · ${f.width} × ${f.height} · sRGB`;$('preview').src=f.image;histogram(f.histogram);$('qualityStatus').textContent=f.quality?.message||'等待画质检查';schedulePreview(0);}
async function removeFile(f){if(exportBusy||importBusy)return;try{await api('/api/close',{id:f.id});files.splice(files.indexOf(f),1);if(current===f)selectFile(files[0]||null);else drawLibrary();persistWorkspace();}catch(e){toast(e.message);}}
async function importFiles(list){if(importBusy||exportBusy){toast('请等待当前处理完成');return;}const incoming=Array.from(list);if(!incoming.length)return;importBusy=true;setEnabled();let succeeded=0;const colorWarnings=[];for(const file of incoming){if(files.length>=8){toast('最多同时打开 8 张照片，请先移除照片');break;}if(!/\.(nef|jpg|jpeg|png)$/i.test(file.name)){toast('不支持 '+file.name+'，请选择 NEF、JPG 或 PNG');continue;}if(file.size>150*1024*1024){toast(file.name+' 超过 150 MB');continue;}busy(true,'正在解码 '+file.name+'…');try{const r=await api('/api/open',file,true,{'X-Filename':encodeURIComponent(file.name)});const d=await r.json();colorWarnings.push(...(d.warnings||[]));const f={...d,settings:cloneEdit(defaults),preset:'neutral',thumb:d.image};f.history=new EditHistory(f.settings,f.preset);files.push(f);selectFile(f);succeeded++;}catch(e){toast(file.name+'：'+e.message);}}importBusy=false;setEnabled();busy(false);if(succeeded)toast(`已导入 ${succeeded} 张照片，原文件保持不变`+(colorWarnings.length?"；"+[...new Set(colorWarnings)].join("；"):""));if(previewWanted)runPreview();$('fileInput').value='';}
function schedulePreview(delay=180){recordEdit();revision++;persistWorkspace();previewWanted=true;clearTimeout(previewTimer);previewTimer=setTimeout(runPreview,delay);$('renderState').textContent='等待更新…';}
async function runPreview(){if(previewRunning||importBusy||exportBusy||!current)return;previewRunning=true;previewWanted=false;const f=current,rev=revision;busy(true);const p={...f.settings};try{const d=await(await api('/api/preview',{id:f.id,settings:p,before:true})).json();if(rev===revision&&current===f){f.image=d.image;f.before=d.before;f.histogram=d.histogram;f.previewWidth=d.width;f.previewHeight=d.height;if(!showingBefore)$('preview').src=d.image;else $('preview').src=d.before;histogram(d.histogram);$('qualityStatus').textContent=d.quality?.message||'未发现明显的输出异常';$('renderState').textContent=`已更新 · 预览 ${d.width} × ${d.height}`;}}catch(e){toast(e.message);$('renderState').textContent='预览失败';}finally{previewRunning=false;if(!importBusy&&!exportBusy)busy(false);if(previewWanted)runPreview();}}
function compare(on){if(!current)return;showingBefore=on;$('preview').src=on?current.before:current.image;$('beforeLabel').hidden=!on;$('compare').classList.toggle('on',on);}
['importTop','importSide','importEmpty'].forEach(id=>$(id).onclick=()=>$('fileInput').click());
$('fileInput').onchange=e=>importFiles(e.target.files);
document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{if(!current)return;current.settings=applyGlobalPreset(current.settings,defaults,presets[b.dataset.preset]);current.preset=b.dataset.preset;syncLabels();schedulePreview(0);});
['wb','crop'].forEach(id=>$(id).onchange=()=>{if(!current)return;current.settings[id]=$(id).value;current.preset='custom';syncLabels();schedulePreview(0);});
$('rotate').onclick=()=>{current.settings.rotation=(current.settings.rotation+1)%4;schedulePreview(0);};
$('reset').onclick=()=>{if(!current)return;current.settings=cloneEdit(defaults);current.preset='neutral';syncLabels();schedulePreview(0);};
$('compare').onpointerdown=e=>{e.preventDefault();$('compare').setPointerCapture(e.pointerId);compare(true);};
$('compare').onpointerup=$('compare').onpointercancel=()=>compare(false);
$('compare').onclick=e=>{if(e.detail===0){compare(!showingBefore);}};
window.addEventListener('keydown',e=>{if(e.code==='Space'&&!/INPUT|SELECT|TEXTAREA|BUTTON/.test(document.activeElement.tagName)&&!$('exportDialog').open&&!$('assistantDialog').open){e.preventDefault();compare(true);}});
window.addEventListener('keyup',e=>{if(e.code==='Space')compare(false);});window.addEventListener('blur',()=>compare(false));
$('zoom').onclick=()=>{if(!current)return;inspector={source:current,settings:cloneEdit(current.settings),cx:.5,cy:.5};$('inspectMap').src=current.image;$('inspectDialog').showModal();loadInspection();};
let dragDepth=0;document.addEventListener('dragenter',e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();dragDepth++;$('dropOverlay').hidden=false;}});document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('dropOverlay').hidden=true;}});document.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('dropOverlay').hidden=true;importFiles(e.dataTransfer.files);});
function download(blob,name){const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),60000);}
$('saveRecipe').onclick=()=>{if(!current)return;download(new Blob([JSON.stringify({app:'Z30 Studio',version:1,source:current.name,settings:current.settings},null,2)],{type:'application/json'}),current.name.replace(/\.[^.]+$/,'')+'-z30.json');toast('参数已下载，可在下次导入照片后载入');};
$('loadRecipe').onclick=()=>$('recipeInput').click();
$('recipeInput').onchange=async e=>{
 try{if(!current||!e.target.files[0])return;const source=current,rev=revision;const file=e.target.files[0];if(file.size>100000)throw Error('参数文件过大');
  const data=JSON.parse(await file.text());if(data.app!=='Z30 Studio'||data.version!==1||!data.settings)throw Error('参数文件无效');
  const validated=(await(await api('/api/settings',{id:source.id,settings:data.settings})).json()).settings;
  if(current!==source||revision!==rev||!files.includes(source))throw Error('照片或参数已改变，未载入旧参数');
  source.settings=validated;
  current.preset='custom';syncLabels();schedulePreview(0);toast('参数、蒙版和镜头修正已载入');
 }catch(error){toast(error.message);}finally{e.target.value='';}
};
$('exportTop').onclick=()=>$('exportDialog').showModal();
$('quality').oninput=()=>$('qualityValue').textContent=$('quality').value;
$('exportFormat').onchange=()=>$('qualityRow').hidden=$('exportFormat').value!=='JPEG';
localFetch('/api/health').then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{$('engine').textContent='LibRaw '+d.libraw+' · 就绪';}).catch(()=>{$('engine').textContent='解码服务连接失败';toast('请通过 start.bat 启动本地服务，再打开网页');});
function syncAssistantControls(){
 const locked=importBusy||exportBusy||!!autoGrader?.busy||!!batchGrader?.busy;$('providerOpen').disabled=locked||assistant.running;
 $('autoGrade').disabled=!current||locked||assistant.running;
 $('autoGrade').textContent=autoGrader?.busy?'正在自动调色…':current?.autoPending?'重新连接分析任务':'一键 AI 调色';
 $('autoCancel').hidden=!autoGrader?.busy&&!batchGrader?.busy;
 $('batchOpen').disabled=!files.length||locked||assistant.running;
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
 assistant.checking=true;$('codexStatus').textContent='正在检测当前模型后端…';syncAssistantControls();
 try{const r=await localFetch('/api/codex/status');if(!r.ok)throw Error('连接检查失败');assistant.connection=await r.json();$('codexStatus').textContent=assistant.connection.message;}
 catch(e){assistant.connection={ready:false};$('codexStatus').textContent='本地服务连接失败，请检查启动窗口后重试';}
 finally{assistant.checking=false;syncAssistantControls();}
}
function assistantCandidate(){
 const t=assistant.strength/100,p=cloneEdit(assistant.base);
 if(t===0)return p;
 sliderSpecs.forEach(([,id,,min,max,step])=>{
  if(!['exposure','contrast','highlights','shadows','temperature','tint','saturation','vibrance','sharpen'].includes(id))return;
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
 if(!current||assistant.running||(autoGrader?.busy||batchGrader?.busy)||!assistant.connection?.ready)return;
 const source=current,base=cloneEdit(current.settings),goal=$('aiGoal').value.trim();
 if(!goal){toast('请先输入调色目标');return;}
 assistant.running=true;assistant.source=source;assistant.base=base;assistant.result=null;assistant.strength=100;assistant.previewSignature=null;
 assistant.trialRevision++;assistant.trialWanted=false;clearTimeout(assistant.trialTimer);
 $('aiResult').hidden=true;$('aiEmpty').hidden=false;$('aiProgress').textContent='正在准备预览并发送给当前模型后端…';syncAssistantControls();
 try{
  const d=await(await api('/api/codex/suggest',{id:source.id,settings:base,goal})).json();
  assistant.currentImage=d.current_image;
  const start=Date.now();let job;
  while(true){
   if(Date.now()-start>210000)throw Error('等待建议超时，请稍后重新检测 Codex 后再试');
   await new Promise(resolve=>setTimeout(resolve,1100));
   const response=await localFetch('/api/codex/jobs/'+encodeURIComponent(d.job));
   if(!response.ok){const error=await response.json();throw Error(error.error||'无法读取建议任务，请重新生成');}job=await response.json();
   if(job.state==='done')break;
   if(job.state==='error'||job.state==='cancelled')throw Error(job.error||'照片会话已关闭');
   $('aiProgress').textContent=`AI 正在看图与分析… ${Math.round((Date.now()-start)/1000)} 秒。关闭面板后任务仍会继续。`;
  }
  if(job.source_id!==source.id)throw Error('建议与当前照片不匹配，请重新生成');
  assistant.result=job.result;
  if(files.includes(source)){source.autoResult={base:cloneEdit(base),result:cloneEdit(job.result),currentImage:assistant.currentImage};persistWorkspace();}
  showAdvice();
  $('aiProgress').textContent='建议已就绪。调整强度、查看试片，再决定是否应用。';
  if(!$('assistantDialog').open)toast('AI 建议已就绪，点击“AI 辅助”查看');
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
 current.aiUndo={settings:cloneEdit(current.settings),preset:current.preset};
 current.settings=assistantCandidate();current.preset='custom';syncLabels();schedulePreview(0);syncAssistantControls();
 $('assistantDialog').close();toast('已应用 AI 建议，可在辅助面板恢复应用前参数');
};
function undoAI(){
 if(!current?.aiUndo||importBusy||exportBusy||autoGrader?.busy||assistant.running)return;
 current.settings=cloneEdit(current.aiUndo.settings);current.preset=current.aiUndo.preset;delete current.aiUndo;
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
  const response=await localFetch('/api/exports');const data=await response.json();if(!response.ok)throw Error(data.error||'无法读取后台任务');
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
 const body={ids:sources.map(f=>f.id),settings:cloneEdit(current.settings),format:$('exportFormat').value,
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
const gradeIO={
 submit:async context=>{
  assistant.trialRevision++;assistant.trialWanted=false;clearTimeout(assistant.trialTimer);
  const data=await(await autoPost('/api/codex/auto',{id:context.source.id,settings:context.base,request_id:context.requestId,auto_options:context.autoOptions||autoOptions(),backend_revision:context.providerRevision})).json();
  context.currentImage=data.current_image;return data;
 },
 recover:async context=>{
  const response=await localFetch('/api/codex/requests/'+encodeURIComponent(context.requestId)+'?source='+encodeURIComponent(context.source.id),{signal:AbortSignal.timeout(15000)});
  const data=await response.json();if(!response.ok)throw Object.assign(Error(data.error||'无法恢复分析任务'),{status:response.status,retryable:response.status>=500});return data;
 },
 poll:async job=>{
  const response=await localFetch('/api/codex/jobs/'+encodeURIComponent(job),{signal:AbortSignal.timeout(15000)});
  const data=await response.json();if(!response.ok)throw Object.assign(Error(data.error||'无法读取自动调色任务'),{status:response.status,retryable:response.status>=500});return data;
 },
 cancel:(context,job)=>autoPost('/api/codex/cancel',{id:context.source.id,job}),
 preview:async(context,settings)=>{
  const data=await(await autoPost('/api/preview',{id:context.source.id,settings,before:true})).json();
  const image=new Image();image.src=data.image;await image.decode();return data;
 },
 isCurrent:context=>canApplyAuto(context,current,files,revision),
 apply:(context,result,preview)=>{
  const f=context.source;
  f.aiUndo={settings:cloneEdit(f.settings),preset:f.preset};
  f.settings=cloneEdit(result.settings);f.preset='custom';recordEdit(f,'AI 自动调色');
  Object.assign(f,{image:preview.image,before:preview.before,histogram:preview.histogram,quality:preview.quality,thumb:preview.image});
  if(current!==f){drawLibrary();persistWorkspace();return;}
  // Invalidate a pending old preview before publishing the verified new image.
  revision++;clearTimeout(previewTimer);previewWanted=false;showingBefore=false;
  Object.assign(f,{image:preview.image,before:preview.before,histogram:preview.histogram,quality:preview.quality});
  $('beforeLabel').hidden=true;$('preview').src=f.image;histogram(f.histogram);
  $('qualityStatus').textContent=preview.quality.message;
  $('renderState').textContent=`AI 自动调色已应用 · 预览 ${preview.width} × ${preview.height}`;
  syncLabels();
 },
 onState:state=>{
  const labels={preparing:'正在准备预览…',analyzing:'AI 正在分析画面与调色…',checking:'正在检查通道、层次和色彩…',rendering:'正在验证本地调色预览…',cancelling:'正在取消；不会自动应用结果。',reconnecting:'连接暂时中断，正在取回原分析任务，不会重新调用模型…'};
  const context=state.context||autoContext;
  if(state.result&&files.includes(context.source))context.source.autoResult={base:context.base,result:state.result,currentImage:context.currentImage};
  if(['done','stale','error','cancelled'].includes(state.state)&&context){
   if(state.pending)context.source.autoPending=context;else delete context.source.autoPending;
  }
  let message=labels[state.state]||state.message;
  if(state.state==='done')message='已自动应用。'+state.result.summary+' '+state.result.quality.message;
  if(state.state==='stale')message='照片或参数已改变，未自动应用。可回到原照片查看分析。';
  $('autoStatus').textContent=(context?context.source.name+' · ':'')+(message||'自动调色未完成');
  $('autoGrade').textContent=autoGrader?.busy?'正在自动调色…':current?.autoPending?'重新连接分析任务':'一键 AI 调色';
  persistWorkspace();syncAssistantControls();
  if(['done','stale','error','cancelled'].includes(state.state))toast(message);
 }
};
autoGrader=new AutoGrader(gradeIO);
$('autoGrade').onclick=()=>{
 if(!current||autoGrader.busy||batchGrader?.busy||assistant.running||importBusy||exportBusy)return;
 autoContext=current.autoPending?{...current.autoPending,recoverOnly:true}:{source:current,base:cloneEdit(current.settings),revision,providerRevision:aiProvider?.revision,autoOptions:autoOptions(),requestId:crypto.randomUUID().replaceAll('-','')};
 current.autoPending=autoContext;persistWorkspace();
 autoGrader.start(autoContext).catch(e=>toast(e.message));
};
$('autoCancel').onclick=()=>batchGrader?.busy?batchGrader.cancel():autoGrader.cancel();
$('autoDetails').onclick=()=>{
 if(!current?.autoResult||autoGrader.busy||assistant.running)return;
 const saved=current.autoResult;
 Object.assign(assistant,{source:current,base:saved.base,result:saved.result,currentImage:saved.currentImage,strength:100,previewSignature:null});
 $('aiProgress').textContent='这是自动调色的分析与画质检查记录。';showAdvice();
 $('assistantDialog').showModal();syncAssistantControls();
 if(!assistant.connection)checkCodex();
};
setEnabled();

async function restoreWorkspace(){
 const saved=savedWorkspace();importBusy=true;setEnabled();
 try{
  const data=await(await api('/api/sessions/restore',{recipes:saved.recipes||{}})).json();
  for(const photo of data.photos){
   const recipe=saved.recipes?.[photo.id]||{};
   const f={...photo,preset:recipe.preset||'custom',thumb:photo.image};
   f.history=new EditHistory(f.settings,f.preset,photo.history);
   if(photo.undo)f.aiUndo=photo.undo;
   if(recipe.pending?.requestId)f.autoPending={source:f,base:recipe.pending.base,requestId:recipe.pending.requestId,revision:-1,recoverOnly:true};
   files.push(f);
  }
  selectFile(files.find(f=>f.id===saved.selected)||files[0]||null);
  if(!files.length&&Object.keys(saved.recipes||{}).length)toast('照片会话已过期或服务已重启，请打开此前保存的项目；也可重新导入原片并载入参数。');
 }catch(e){toast('恢复照片会话失败：'+e.message);}
 finally{importBusy=false;setEnabled();if(previewWanted)runPreview();}
}
restoreWorkspace();
setInterval(()=>api('/api/sessions/heartbeat',{}).catch(()=>{}),30000);

var historyApplying=false;
function recordEdit(f=current,label='调整参数'){
 if(!f||historyApplying)return;
 if(!f.history)f.history=new EditHistory(f.settings,f.preset);
 if(label==='调整参数'){const previous=f.history.states[f.history.index];const changed=Object.keys(f.settings).filter(k=>JSON.stringify(previous.settings[k])!==JSON.stringify(f.settings[k]));const labels={masks:'局部蒙版',crop:'构图裁剪',rotation:'旋转',wb:'白平衡',lens_profile:'镜头配置'};const presetsNames={neutral:'自然',portrait:'柔和人像',travel:'晴日旅行',film:'暖调胶片',mono:'经典黑白'};label=f.preset!=='custom'&&presetsNames[f.preset]?'预设：'+presetsNames[f.preset]:changed.length===1?(sliderSpecs.find(x=>x[1]===changed[0])?.[2]||labels[changed[0]]||'调整参数'):'多个参数';}
 if(f.history.capture(f.settings,f.preset,label))f.editVersion=(f.editVersion||0)+1;
 syncHistory();
}
function syncHistory(){
 const h=current?.history,locked=importBusy||exportBusy;
 $('historyUndo').disabled=!h||h.index===0||locked;
 $('historyRedo').disabled=!h||h.index===h.states.length-1||locked;
 $('historyList').disabled=!h||locked;$('saveProject').disabled=!files.length||locked;
 $('historyList').replaceChildren();
 if(h)h.states.forEach((entry,index)=>{const option=document.createElement('option');option.value=index;option.textContent=(index+1)+'. '+entry.label;option.selected=index===h.index;$('historyList').append(option);});
}
function moveHistory(index){
 if(!current?.history||importBusy||exportBusy)return;
 const state=current.history.go(index);current.settings=state.settings;current.preset=state.preset;
 current.editVersion=(current.editVersion||0)+1;historyApplying=true;
 try{syncLabels();schedulePreview(0);}finally{historyApplying=false;}
}
$('historyUndo').onclick=()=>moveHistory(current.history.index-1);
$('historyRedo').onclick=()=>moveHistory(current.history.index+1);
$('historyList').onchange=e=>moveHistory(Number(e.target.value));
window.addEventListener('keydown',e=>{
 if(!(e.ctrlKey||e.metaKey)||/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName))return;
 if(e.key.toLowerCase()==='z'){e.preventDefault();if(current?.history)moveHistory(current.history.index+(e.shiftKey?1:-1));}
 if(e.key.toLowerCase()==='y'){e.preventDefault();if(current?.history)moveHistory(current.history.index+1);}
});
$('saveProject').onclick=async()=>{
 if(!files.length||importBusy||exportBusy)return;
 recordEdit();const recipes={};for(const f of files)recipes[f.id]={settings:f.settings,preset:f.preset,undo:f.aiUndo||null,history:f.history?.snapshot(),analysis:f.autoResult?{base:f.autoResult.base,result:f.autoResult.result}:null};
 try{const data=await(await api('/api/projects/prepare',{ids:files.map(f=>f.id),recipes,selected:files.indexOf(current)})).json();
  const link=document.createElement('a');link.href=data.url;link.download='Z30-Project.z30proj';document.body.append(link);link.click();link.remove();
  toast('项目含原片副本，请保管本机下载文件；下载完成前勿移除照片');
 }catch(e){toast(e.message);}
};
$('openProject').onclick=()=>$('projectInput').click();
$('projectInput').onchange=async e=>{
 const file=e.target.files[0];if(!file)return;
 if(file.size>512*1024*1024){toast('项目超过 512 MiB');return;}
 importBusy=true;setEnabled();
 try{const data=await(await api('/api/projects/open',file,true)).json();const added=[];
  for(const photo of data.photos){const f={...photo,thumb:photo.image};f.history=new EditHistory(f.settings,f.preset,photo.history);if(photo.undo)f.aiUndo=photo.undo;
   if(photo.analysis?.result)f.autoResult={base:photo.analysis.base,result:photo.analysis.result,currentImage:photo.image};
   files.push(f);added.push(f);
  }selectFile(added[data.selected]||added[0]);toast('项目原片、参数与历史已恢复');
 }catch(error){toast(error.message);}finally{importBusy=false;setEnabled();if(previewWanted)runPreview();e.target.value='';}
};

var inspector=null,inspectRevision=0;
async function loadInspection(){
 if(!inspector)return;const job=inspector,rev=++inspectRevision;
 $('inspectStatus').textContent='正在读取全尺寸显影；此视图包含当前调色与构图…';
 try{const data=await(await api('/api/inspect',{id:job.source.id,settings:job.settings,cx:job.cx,cy:job.cy})).json();
  if(rev!==inspectRevision||!$('inspectDialog').open)return;
  inspector={...job,...data};$('inspectImage').src=data.image;$('inspectImage').width=data.width;$('inspectImage').height=data.height;
  $('inspectStatus').textContent=`原尺寸 ${data.full_width} × ${data.full_height} · 区域 (${data.x}, ${data.y}) ${data.width} × ${data.height} · PNG 无损预览`;
 }catch(e){$('inspectStatus').textContent=e.message;}
}
$('inspectClose').onclick=()=>{$('inspectDialog').close();inspectRevision++;};
$('inspectMap').onclick=e=>{if(!inspector)return;const r=e.target.getBoundingClientRect();inspector.cx=(e.clientX-r.left)/r.width;inspector.cy=(e.clientY-r.top)/r.height;loadInspection();};
document.querySelectorAll('[data-pan]').forEach(button=>button.onclick=()=>{
 if(!inspector?.full_width)return;const [dx,dy]=button.dataset.pan.split(',').map(Number);
 inspector.cx=Math.max(0,Math.min(1,inspector.cx+dx*inspector.width/inspector.full_width*.7));
 inspector.cy=Math.max(0,Math.min(1,inspector.cy+dy*inspector.height/inspector.full_height*.7));loadInspection();
});

function autoOptions(){return {style:$('autoStyle').value,strength:Number($('autoMaxStrength').value),exposure_limit:Number($('autoExposureLimit').value),color_limit:Number($('autoColorLimit').value),goal:$('autoGoalExtra').value,reference:''};}
try{const saved=JSON.parse(localStorage.getItem('z30-auto-preferences')||'null');if(saved){for(const [id,key] of [['autoStyle','style'],['autoMaxStrength','strength'],['autoExposureLimit','exposure_limit'],['autoColorLimit','color_limit']]){if([...$(id).options].some(o=>o.value===String(saved[key])))$(id).value=saved[key];}if(typeof saved.goal==='string')$('autoGoalExtra').value=saved.goal.slice(0,350);}}catch{}
['autoStyle','autoMaxStrength','autoExposureLimit','autoColorLimit','autoGoalExtra'].forEach(id=>$(id).addEventListener('change',()=>{try{localStorage.setItem('z30-auto-preferences',JSON.stringify(autoOptions()));}catch{}}));
function batchStillCurrent(context){return files.includes(context.source)&&(!context.source.autoPending||context.source.autoPending.requestId===context.requestId)&&(context.source.editVersion||0)===context.fileRevision&&JSON.stringify(context.source.settings)===JSON.stringify(context.base);}
let batchItems=[];
function drawBatch(){
 $('batchItems').replaceChildren(...batchItems.map(item=>{const li=document.createElement('li');li.textContent=item.name+' · '+item.state;return li;}));
 $('batchStart').disabled=!!batchGrader?.busy||!!autoGrader?.busy||assistant.running||!files.length;
 $('batchStop').disabled=!batchGrader?.busy;$('batchBudget').disabled=!!batchGrader?.busy;
}
batchGrader=new BatchGrader({
 isCurrent:batchStillCurrent,
 make:(context,index)=>{context.source.autoPending=context;persistWorkspace();return new AutoGrader({...gradeIO,isCurrent:batchStillCurrent,onState:state=>{
  gradeIO.onState({...state,context});batchItems[index].state=state.state==='done'?'已应用':state.state==='stale'?'编辑已改变，跳过':state.message||({preparing:'准备预览',submitting:'提交分析',analyzing:'分析中',rendering:'渲染验证',checking:'画质检查',cancelling:'取消中'})[state.state]||state.state;drawBatch();
 }});},
 onItem:(index,outcome)=>{batchItems[index].state=({done:'已应用',stale:'编辑已改变，跳过',error:'失败：'+outcome.message,cancelled:'已取消'})[outcome.state]||outcome.state;drawBatch();},
 onFinish:(outcomes,spent)=>{outcomes.forEach((o,i)=>{if(o.state==='not-started')batchItems[i].state='未开始（上限或已停止）';});$('batchStatus').textContent=`本次已启动 ${spent} 次分析，成功应用 ${outcomes.filter(x=>x.state==='done').length} 张。`;drawBatch();syncAssistantControls();}
});
$('batchOpen').onclick=()=>{if(!batchItems.length)$('batchBudget').value=Math.max(1,files.length);$('batchDialog').showModal();drawBatch();};
$('batchClose').onclick=()=>$('batchDialog').close();
$('batchStop').onclick=()=>batchGrader.cancel();
$('batchStart').onclick=async()=>{
 if(batchGrader.busy||autoGrader.busy||assistant.running)return;
 const options=autoOptions();const contexts=files.map(f=>({source:f,base:cloneEdit(f.settings),fileRevision:f.editVersion||0,providerRevision:aiProvider?.revision,autoOptions:options,requestId:crypto.randomUUID().replaceAll('-','')}));
 batchItems=contexts.map(c=>({name:c.source.name,state:'等待'}));$('batchStatus').textContent='顺序逐张分析，可停止；编辑中的照片若有变化会跳过。';drawBatch();
 try{await batchGrader.run(contexts,Number($('batchBudget').value));}catch(e){toast(e.message);}
};

var activeMask=-1,maskEditing=false,maskDrag=null;
var maskSliderSpecs=[['exposure','局部曝光',-2,2,.05],['shadows','局部阴影',-100,100,1],['temperature','局部色温',-60,60,1],['saturation','局部饱和度',-100,100,1],['opacity','不透明度',0,1,.05],['feather','羽化',.01,1,.01],['radius','画笔大小',.005,.3,.005]];
for(const [key,label,min,max,step] of maskSliderSpecs){
 const row=document.createElement('label');row.className='mask-slider';row.textContent=label;
 const input=document.createElement('input');Object.assign(input,{id:'mask-'+key,type:'range',min,max,step});input.setAttribute('aria-label',label);
 const output=document.createElement('output');output.id='mask-value-'+key;
 input.oninput=()=>{const mask=current?.settings.masks?.[activeMask];if(!mask||importBusy||exportBusy)return;mask[key]=Number(input.value);current.preset='custom';schedulePreview();syncMaskUI();};
 row.append(input,output);$('maskSliders').append(row);
}
function newMask(type){return {type,enabled:true,invert:false,x0:.2,y0:.2,x1:.8,y1:.8,cx:.5,cy:.5,rx:.25,ry:.25,radius:.06,feather:.6,opacity:1,exposure:.5,shadows:0,temperature:0,saturation:0,points:[],breaks:[]};}
function syncMaskUI(){
 const masks=current?.settings.masks||[];activeMask=masks.length?Math.max(0,Math.min(Number.isInteger(activeMask)?activeMask:0,masks.length-1)):-1;
 $('maskList').replaceChildren(...masks.map((m,i)=>{const o=document.createElement('option');o.value=i;o.textContent=(i+1)+'. '+({linear:'渐变',radial:'径向',brush:'画笔'}[m.type]);o.selected=i===activeMask;return o;}));
 const mask=masks[activeMask];const locked=!current||importBusy||exportBusy;
 for(const id of ['maskDraw','maskDelete','maskEnabled','maskInvert','maskList'])$(id).disabled=locked||!mask;
 $('maskEnabled').checked=mask?.enabled??true;$('maskInvert').checked=mask?.invert??false;
 for(const [key] of maskSliderSpecs||[]){const input=$('mask-'+key);if(!input)continue;input.disabled=locked||!mask;input.value=mask?.[key]??0;$('mask-value-'+key).textContent=Number(mask?.[key]??0).toFixed(key==='exposure'?2:3);}
 $('maskDraw').textContent=maskEditing?'完成绘制':'在照片上绘制';paintMasks();
}
function paintMasks(){
 const canvas=$('maskCanvas'),mask=current?.settings.masks?.[activeMask];canvas.hidden=!maskEditing||!mask;
 if(canvas.hidden)return;
 const image=$('preview').getBoundingClientRect(),stage=$('stage').getBoundingClientRect();
 if(!image.width||!image.height)return;
 Object.assign(canvas.style,{left:(image.left-stage.left)+'px',top:(image.top-stage.top)+'px',width:image.width+'px',height:image.height+'px'});
 const scale=devicePixelRatio||1;canvas.width=Math.round(image.width*scale);canvas.height=Math.round(image.height*scale);
 const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.strokeStyle='#f3d071';ctx.fillStyle='#edcb6838';ctx.lineWidth=2;
 const x=v=>v*image.width,y=v=>v*image.height;
 if(mask.type==='linear'){ctx.beginPath();ctx.moveTo(x(mask.x0),y(mask.y0));ctx.lineTo(x(mask.x1),y(mask.y1));ctx.stroke();for(const p of [[mask.x0,mask.y0],[mask.x1,mask.y1]]){ctx.beginPath();ctx.arc(x(p[0]),y(p[1]),5,0,Math.PI*2);ctx.stroke();}}
 if(mask.type==='radial'){ctx.beginPath();ctx.ellipse(x(mask.cx),y(mask.cy),x(mask.rx),y(mask.ry),0,0,Math.PI*2);ctx.fill();ctx.stroke();}
 if(mask.type==='brush'&&mask.points.length){ctx.lineWidth=mask.radius*Math.min(image.width,image.height)*2;ctx.lineCap='round';ctx.lineJoin='round';ctx.strokeStyle='#edcb6866';ctx.beginPath();const starts=new Set([0,...(mask.breaks||[])]);mask.points.forEach((p,i)=>{if(starts.has(i)){ctx.moveTo(x(p[0]),y(p[1]));ctx.lineTo(x(p[0])+.01,y(p[1]));}else ctx.lineTo(x(p[0]),y(p[1]));});ctx.stroke();}
}
document.querySelectorAll('[data-new-mask]').forEach(button=>button.onclick=()=>{
 if(!current||importBusy||exportBusy)return;if(current.settings.masks.length>=8){toast('最多 8 个蒙版');return;}
 current.settings.masks.push(newMask(button.dataset.newMask));activeMask=current.settings.masks.length-1;maskEditing=true;syncMaskUI();schedulePreview(0);
});
$('maskList').onchange=e=>{activeMask=Number(e.target.value);syncMaskUI();};
$('maskDraw').onclick=()=>{maskEditing=!maskEditing;syncMaskUI();};
$('maskDelete').onclick=()=>{if(!current)return;current.settings.masks.splice(activeMask,1);activeMask--;syncMaskUI();schedulePreview(0);};
for(const [id,key] of [['maskEnabled','enabled'],['maskInvert','invert']])$(id).onchange=()=>{const mask=current?.settings.masks[activeMask];if(mask){mask[key]=$(id).checked;schedulePreview(0);}};
function maskPoint(event){const rect=$('maskCanvas').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))].map(x=>Math.round(x*100000)/100000);}
$('maskCanvas').onpointerdown=e=>{
 const mask=current?.settings.masks[activeMask];if(!mask)return;e.preventDefault();$('maskCanvas').setPointerCapture(e.pointerId);
 const point=maskPoint(e);maskDrag={source:current,mask,start:point};
 if(mask.type==='brush'){
  if(current.settings.masks.reduce((n,m)=>n+m.points.length,0)>=512){maskDrag=null;toast('画笔点已达上限');return;}
  if(mask.points.length)(mask.breaks??=[]).push(mask.points.length);
  mask.points.push(point);
 }else if(mask.type==='linear'){[mask.x0,mask.y0]=point;[mask.x1,mask.y1]=point;}
 else{[mask.cx,mask.cy]=point;mask.rx=.01;mask.ry=.01;}
 paintMasks();
};
$('maskCanvas').onpointermove=e=>{
 if(!maskDrag||maskDrag.source!==current)return;const m=maskDrag.mask,p=maskPoint(e);
 if(m.type==='brush'){
  const last=m.points[m.points.length-1];if(Math.hypot(p[0]-last[0],p[1]-last[1])>m.radius/3&&current.settings.masks.reduce((n,x)=>n+x.points.length,0)<512)m.points.push(p);
 }else if(m.type==='linear'){[m.x1,m.y1]=p;}else{m.rx=Math.max(.01,Math.abs(p[0]-m.cx));m.ry=Math.max(.01,Math.abs(p[1]-m.cy));}
 paintMasks();
};
$('maskCanvas').onpointerup=$('maskCanvas').onpointercancel=()=>{if(maskDrag){const source=maskDrag.source;maskDrag=null;if(!files.includes(source))return;source.preset='custom';recordEdit(source,'局部蒙版');persistWorkspace();if(source===current){syncMaskUI();schedulePreview(0);}}};
$('preview').addEventListener('load',paintMasks);window.addEventListener('resize',paintMasks);
$('lensProfileName').onchange=()=>{if(current){current.settings.lens_profile=$('lensProfileName').value;schedulePreview();}};
$('saveLens').onclick=()=>{if(!current)return;const settings={};for(const key of ['lens_k1','lens_k2','lens_red','lens_blue','vignette','lens_profile'])settings[key]=current.settings[key];download(new Blob([JSON.stringify({app:'Z30 Manual Optics',version:1,model:'centered-radial-v1',settings},null,2)],{type:'application/json'}),'Z30-manual-lens.json');};
$('loadLens').onclick=()=>$('lensInput').click();
$('lensInput').onchange=async e=>{try{if(!current||!e.target.files[0])return;const source=current,rev=revision;const update=cloneEdit(source.settings);const file=e.target.files[0];if(file.size>20000)throw Error('配置过大');const data=JSON.parse(await file.text());if(data.app!=='Z30 Manual Optics'||data.version!==1||data.model!=='centered-radial-v1')throw Error('不是兼容的手动镜头配置');for(const key of ['lens_k1','lens_k2','lens_red','lens_blue','vignette','lens_profile'])if(key in data.settings)update[key]=data.settings[key];const validated=(await(await api('/api/settings',{id:source.id,settings:update})).json()).settings;if(current!==source||revision!==rev||!files.includes(source))throw Error('照片或参数已改变，未载入旧镜头配置');source.settings=validated;syncLabels();schedulePreview(0);toast('已载入手动镜头参数，请检查网格和边缘');}catch(error){toast(error.message);}finally{e.target.value='';}};
setEnabled();syncMaskUI();

const modelSettings=new ModelSettings({read:()=>localFetch('/api/ai/config'),post:(path,data)=>api(path,data),busy:()=>importBusy||exportBusy||!!autoGrader?.busy||!!batchGrader?.busy||assistant.running,changed:config=>{
 aiProvider=config;assistant.connection=null;
 const label=config.provider==='codex'?'Codex CLI':config.provider==='deepseek'?'DeepSeek · '+config.model:'兼容接口 · '+config.model;
 $('providerSummary').textContent=label;$('modelBadge').textContent=label;
 const privacy=config.provider==='codex'?'点击分析会发送两张去除 EXIF/GPS 的缩小预览至 Codex，使用现有 CLI 登录与额度。':`点击分析会发送两张去除 EXIF/GPS 的缩小预览至 ${config.base_url}（${config.model}），可能使用该服务商的付费额度。`;
 $('modelPrivacy').textContent=privacy+' 原片留在本机，不自动重试。';document.querySelector('.ai-privacy').textContent=privacy+' 原片留在本机。';$('batchPrivacy').textContent=privacy+' 每张照片独立分析，次数由下方上限约束。';syncAssistantControls();
}});modelSettings.init();
