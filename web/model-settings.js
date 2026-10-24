'use strict';
class ModelSettings {
 constructor(io){this.io=io;this.config=null;this.saving=false;this.el=id=>document.getElementById(id);}
 async init(){
  this.el('providerOpen').onclick=async()=>{if(this.io.busy())return;try{await this.load();this.el('providerDialog').showModal();}catch(e){this.el('providerMessage').textContent=e.message;}};
  this.el('providerClose').onclick=()=>this.el('providerDialog').close();
  this.el('providerSelect').onchange=()=>this.mode();
  this.el('providerSave').onclick=()=>this.save();this.el('providerTest').onclick=()=>this.test();
  try{await this.load();}catch{this.el('providerSummary').textContent='模型设置读取失败，请重新打开设置';}
 }
 async load(){
  const response=await this.io.read();if(!response.ok)throw Error('无法读取模型设置');
  this.config=await response.json();this.io.changed(this.config);
  this.el('providerSelect').value=this.config.provider;this.el('providerUrl').value=this.config.base_url;
  this.el('providerModel').value=this.config.model;this.el('providerKey').value='';this.el('providerClear').checked=false;
  this.el('providerCurrency').value=this.config.rates?.currency||'USD';this.el('providerInputRate').value=this.config.rates?.input??'';this.el('providerOutputRate').value=this.config.rates?.output??'';
  this.el('providerMessage').textContent=this.config.has_key?'密钥已在当前运行中配置；此处不会回显。':'密钥仅保留在当前运行内存中，重启后需重新填写或使用专用环境变量。';this.mode(false);
 }
 mode(reset=true){
  const provider=this.el('providerSelect').value;this.el('providerRemote').hidden=provider==='codex';
  if(provider==='local'&&reset){this.el('providerUrl').value='http://127.0.0.1:11434/v1';this.el('providerModel').value='qwen3-vl:8b';this.el('providerKey').value='';}
  this.el('providerLocalNote').hidden=provider!=='local';this.el('providerKeyRow').hidden=this.el('providerClearRow').hidden=provider==='local';
  if(provider==='local')this.el('providerMessage').textContent='本机模式无需 Key；读取模型列表可选择已安装的视觉模型。尚未验证本机真实推理。';else if(reset)this.el('providerMessage').textContent='请保存当前设置后读取模型列表；模型须支持图片，分析时才发送预览。';
  this.el('providerRates').hidden=['codex','local'].includes(provider);
  if(provider==='deepseek'){this.el('providerUrl').value='https://api.deepseek.com';this.el('providerModel').value='deepseek-flash';}
  this.el('providerUrl').disabled=this.el('providerModel').disabled=!['compatible','local'].includes(provider);
 }
 draft(){return {provider:this.el('providerSelect').value,base_url:this.el('providerUrl').value.trim(),model:this.el('providerModel').value.trim(),api_key:this.el('providerKey').value.trim(),clear_key:this.el('providerClear').checked,rates:{input:this.el('providerInputRate').value===''?null:Number(this.el('providerInputRate').value),output:this.el('providerOutputRate').value===''?null:Number(this.el('providerOutputRate').value),currency:this.el('providerCurrency').value}};}
 async save(){
  if(this.saving||this.io.busy())return;this.saving=true;this.el('providerSave').disabled=true;
  try{const response=await this.io.post('/api/ai/config',this.draft());this.config=await response.json();this.io.changed(this.config);this.el('providerKey').value='';this.el('providerClear').checked=false;this.el('providerMessage').textContent='已保存。接口模式的 API Key 不写入磁盘；点击分析才发送照片预览。';}
  catch(e){this.el('providerMessage').textContent=e.message;}
  finally{this.saving=false;this.el('providerSave').disabled=false;}
 }
 async test(){
  if(this.saving||this.io.busy())return;
  const draft=this.draft();if(!this.config||draft.api_key||draft.clear_key||['provider','base_url','model'].some(k=>draft[k]!==this.config[k])){this.el('providerMessage').textContent='请先保存当前设置，再测试连接。';return;}
  this.el('providerTest').disabled=true;this.el('providerMessage').textContent='正在读取模型列表，不发送照片…';
  try{const result=await(await this.io.post('/api/ai/test',{})).json();const list=this.el('providerModels');list.replaceChildren();for(const name of result.models||[]){const option=document.createElement('option');option.value=name;list.append(option);}this.el('providerMessage').textContent=result.message+(this.config.provider==='codex'?'':result.model_listed?'；列表包含当前模型。':'；列表未包含当前模型，请核对模型名。');}
  catch(e){this.el('providerMessage').textContent=e.message;}
  finally{this.el('providerTest').disabled=false;}
 }
}
