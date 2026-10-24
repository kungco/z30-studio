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
  this.el('providerMessage').textContent=this.config.has_key?'密钥已在当前运行中配置；此处不会回显。':'密钥仅保留在当前运行内存中，重启后需重新填写或使用专用环境变量。';this.mode();
 }
 mode(){
  const provider=this.el('providerSelect').value;this.el('providerRemote').hidden=provider==='codex';
  if(provider==='deepseek'){this.el('providerUrl').value='https://api.deepseek.com';this.el('providerModel').value='deepseek-flash';}
  this.el('providerUrl').disabled=this.el('providerModel').disabled=provider!=='compatible';
 }
 draft(){return {provider:this.el('providerSelect').value,base_url:this.el('providerUrl').value.trim(),model:this.el('providerModel').value.trim(),api_key:this.el('providerKey').value.trim(),clear_key:this.el('providerClear').checked};}
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
  try{const result=await(await this.io.post('/api/ai/test',{})).json();this.el('providerMessage').textContent=result.message+(this.config.provider==='codex'?'':result.model_listed?'；列表包含当前模型。':'；列表未包含当前模型，请核对模型名。');}
  catch(e){this.el('providerMessage').textContent=e.message;}
  finally{this.el('providerTest').disabled=false;}
 }
}
