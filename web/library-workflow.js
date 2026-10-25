'use strict';
function photoCatalog(value={}){return {rating:0,flag:'none',keywords:[],...value};}
function filterPhotos(photos,{query='',rating=0,flag='all'}={}){
 const words=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
 return photos.filter(f=>{const c=photoCatalog(f.catalog),text=(f.name+' '+c.keywords.join(' ')).toLocaleLowerCase();return c.rating>=rating&&(flag==='all'||c.flag===flag)&&words.every(w=>text.includes(w));});
}
class LibraryWorkflow {
 constructor(io){this.io=io;this.clipboard=null;this.busy=false;this.filters={};
  this.$=id=>document.getElementById(id);
  this.$('libraryWorkflow').innerHTML=`<input id="libraryQuery" aria-label="搜索文件名或关键词" placeholder="搜索文件名 / 关键词"><div class="catalog-filters"><select id="ratingFilter" aria-label="最低星级"><option value="0">全部星级</option>${[1,2,3,4,5].map(i=>`<option value="${i}">${i} 星及以上</option>`).join('')}</select><select id="flagFilter" aria-label="旗标筛选"><option value="all">全部旗标</option><option value="pick">已选用</option><option value="reject">已拒绝</option><option value="none">无旗标</option></select></div><details><summary>多选、比较与参数同步</summary><button id="selectVisible">选择筛选结果</button><button id="clearSelection">清空选择</button><small id="selectionCount">已选 0 张</small><button id="compareSelected">并排比较所选两张</button><label>当前照片关键词<input id="photoKeywords" aria-label="当前照片关键词" placeholder="用逗号分隔，最多 32 个"></label><details><summary>复制 / 同步参数组</summary>${[['light','光线'],['color','颜色 / 白平衡 / 曲线'],['detail','细节 / 降噪'],['geometry','裁剪 / 旋转 / 拉直'],['local','局部蒙版 / 修复'],['optics','镜头校正']].map(([v,t],i)=>`<label><input type="checkbox" name="syncGroup" value="${v}" ${i<3?'checked':''}> ${t}</label>`).join('')}<button id="copyEdits">复制当前参数</button><button id="pasteEdits">粘贴到当前照片</button><button id="syncEdits">当前参数同步到所选照片</button><p id="clipboardInfo">默认只同步光线、颜色和细节；不调用 AI。</p></details></details>`;
  this.$('libraryQuery').oninput=()=>{this.filters.query=this.$('libraryQuery').value;io.redraw();};
  this.$('ratingFilter').onchange=()=>{this.filters.rating=Number(this.$('ratingFilter').value);io.redraw();};
  this.$('flagFilter').onchange=()=>{this.filters.flag=this.$('flagFilter').value;io.redraw();};
  this.$('selectVisible').onclick=()=>{for(const f of this.visible(io.photos()))f.multiSelected=true;io.redraw();};
  this.$('clearSelection').onclick=()=>{for(const f of io.photos())f.multiSelected=false;io.redraw();};
  this.$('compareSelected').onclick=()=>io.compare(this.selected());
  this.$('photoKeywords').oninput=()=>{const f=io.current();if(!f)return;const words=this.$('photoKeywords').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean);if(words.length>32||words.some(x=>x.length>48)){io.error('关键词最多 32 个，每个不超过 48 字');return;}f.catalog={...photoCatalog(f.catalog),keywords:[...new Set(words)]};io.persist();};
  this.$('photoKeywords').onblur=()=>io.redraw();
  this.$('copyEdits').onclick=()=>{const f=io.current();if(!f)return;this.clipboard=JSON.parse(JSON.stringify(f.settings));this.$('clipboardInfo').textContent='已复制：'+f.name;this.sync();};
  this.$('pasteEdits').onclick=()=>this.apply(this.clipboard,[io.current()].filter(Boolean));
  this.$('syncEdits').onclick=()=>this.apply(io.current()?.settings,this.selected());
 }
 visible(photos){return filterPhotos(photos,this.filters);}
 selected(){return this.io.photos().filter(f=>f.multiSelected);}
 decorate(card,f){f.catalog=photoCatalog(f.catalog);const row=document.createElement('div');row.className='catalog-row';const selected=document.createElement('input');selected.type='checkbox';selected.checked=!!f.multiSelected;selected.setAttribute('aria-label','选中 '+f.name);selected.onchange=e=>{const visible=this.visible(this.io.photos());if(e.shiftKey&&this.lastSelected&&visible.includes(this.lastSelected)){const a=visible.indexOf(f),b=visible.indexOf(this.lastSelected);for(const photo of visible.slice(Math.min(a,b),Math.max(a,b)+1))photo.multiSelected=selected.checked;}else f.multiSelected=selected.checked;this.lastSelected=f;this.io.redraw();};
  const rating=document.createElement('select');rating.setAttribute('aria-label','星级 '+f.name);for(let i=0;i<=5;i++){const option=document.createElement('option');option.value=i;option.textContent=i?'★'.repeat(i):'未评分';rating.append(option);}rating.value=f.catalog.rating;rating.onchange=()=>{f.catalog.rating=Number(rating.value);this.io.persist();this.io.redraw();};
  const flag=document.createElement('select');flag.setAttribute('aria-label','旗标 '+f.name);for(const [value,label] of [['none','无旗标'],['pick','✓ 选用'],['reject','× 拒绝']]){const o=document.createElement('option');o.value=value;o.textContent=label;flag.append(o);}flag.value=f.catalog.flag;flag.onchange=()=>{f.catalog.flag=flag.value;this.io.persist();this.io.redraw();};
  row.append(selected,rating,flag);card.append(row);}
 sync(){const count=this.selected().length,f=this.io.current();this.$('selectionCount').textContent=`已选 ${count} 张 / 筛选 ${this.visible(this.io.photos()).length} 张`;this.$('compareSelected').disabled=count!==2||this.busy;this.$('copyEdits').disabled=!f||this.busy;this.$('pasteEdits').disabled=!f||!this.clipboard||this.busy;this.$('syncEdits').disabled=!f||!count||this.busy;this.$('photoKeywords').value=photoCatalog(f?.catalog).keywords.join(', ');this.$('photoKeywords').disabled=!f;}
 async apply(source,targets){
  if(this.busy||!source||!targets.length)return;const groups=[...this.$('libraryWorkflow').querySelectorAll('[name=syncGroup]:checked')].map(e=>e.value);if(!groups.length){this.io.error('请选择同步参数组');return;}
  const snapshots=targets.map(f=>({f,signature:JSON.stringify(f.settings)}));this.busy=true;this.sync();
  try{const result=await this.io.validate({source,groups,targets:targets.map(f=>({id:f.id,settings:f.settings}))});
   if(snapshots.some(({f,signature})=>!this.io.photos().includes(f)||JSON.stringify(f.settings)!==signature))throw Error('同步期间照片或参数已改变，本次未应用');
   for(const photo of result.photos){const f=targets.find(f=>f.id===photo.id);f.settings=photo.settings;f.preset='custom';this.io.record(f,'手动同步参数');}
   this.io.applied();this.io.error(`已同步 ${targets.length} 张，可逐张撤销或微调`);
  }catch(e){this.io.error(e.message);}finally{this.busy=false;this.sync();}
 }
}
if(typeof module!=='undefined')module.exports={photoCatalog,filterPhotos,LibraryWorkflow};
