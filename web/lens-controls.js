'use strict';
class LensControls {
 constructor(io){this.io=io;this.busy=false;this.data=null;this.$=id=>document.getElementById(id);
  this.$('lensAutoMount').innerHTML=`<details class="advanced-panel"><summary>自动镜头配置 · 横向色差估计</summary><p id="lensDetected" class="controlnote">导入照片后读取镜头与焦距。</p><select id="lensProfileSelect" aria-label="镜头校准配置"><option value="">按 EXIF 自动匹配</option></select><label>焦距 mm<input id="lensFocal" type="number" min="1" max="2000" step=".1"></label><label>光圈 f/<input id="lensAperture" type="number" min=".5" max="64" step=".1"></label><label>传感器裁切系数<input id="lensCropFactor" type="number" min=".5" max="10" step=".001"></label><label>对焦距离 m（未知按远处）<input id="lensDistance" type="number" min=".1" max="10000" value="1000"></label><button id="lensAutoApply">匹配并应用配置</button><label><input id="lensAutoEnabled" type="checkbox"> 启用已保存镜头配置</label><label><input id="lensFill" type="checkbox" checked> 自动填满画面（会裁去部分边缘）</label><label><input id="lensTca" type="checkbox" checked> 应用配置的横向色差</label><label><input id="lensVignette" type="checkbox"> 配置暗角补偿（有数据时）</label><button id="lensEstimateTca">基于图像估计横向色差</button><p id="lensAutoStatus" class="controlnote">Lensfun 数据库配置；没有匹配就不猜测。应用配置会重置当前手动镜头系数，支持撤销。尚未做本机实镜网格标定。</p></details>`;
  this.$('lensAutoApply').onclick=()=>this.apply();this.$('lensEstimateTca').onclick=()=>this.estimate();
  for(const [id,key] of [['lensAutoEnabled','lens_auto'],['lensFill','lens_fill'],['lensTca','lens_tca'],['lensVignette','lens_vignette']])this.$(id).onchange=()=>io.change({[key]:this.$(id).checked});
 }
 async init(){try{this.data=await this.io.read();for(const p of this.data.profiles){const option=document.createElement('option');option.value=p.id;option.textContent=p.name;this.$('lensProfileSelect').append(option);}this.sync(true);}catch(e){this.$('lensAutoStatus').textContent=e.message;}}
 sync(reset=false){const f=this.io.current(),p=f?.settings||{},locked=!f||this.io.locked()||this.busy;this.$('lensAutoMount').querySelectorAll('button,input,select').forEach(e=>e.disabled=locked);
  if(reset||this.source!==f){this.source=f;this.$('lensDetected').textContent=f?.lens_model?`${f.lens_model} · ${f.focal_mm||'?'} mm`:'没有完整镜头 EXIF，请手动选择和填写；不会套用猜测配置。';this.$('lensFocal').value=f?.focal_mm||'';this.$('lensAperture').value=f?.aperture||8;this.$('lensCropFactor').value=f?.sensor_crop||'';this.$('lensDistance').value=f?.focus_m||1000;this.$('lensProfileSelect').value='';}
  for(const [id,key] of [['lensAutoEnabled','lens_auto'],['lensFill','lens_fill'],['lensTca','lens_tca'],['lensVignette','lens_vignette']])this.$(id).checked=p[key]??!['lens_auto','lens_vignette'].includes(key);
  this.$('lensAutoEnabled').disabled=locked||!p.lens_calibration;this.$('lensVignette').disabled=locked||!p.lens_calibration?.vignetting?.some(v=>v!==0);
 }
 async apply(){if(this.busy||!this.io.current())return;this.busy=true;this.sync();const f=this.io.current(),signature=JSON.stringify(f.settings);
  try{const options={profile:this.$('lensProfileSelect').value};for(const [id,key] of [['lensFocal','focal'],['lensAperture','aperture'],['lensCropFactor','crop'],['lensDistance','distance']])if(this.$(id).value!=='')options[key]=Number(this.$(id).value);
   const result=await this.io.apply(f,options);if(this.io.current()!==f||JSON.stringify(f.settings)!==signature)throw Error('编辑已改变，未应用过期镜头配置');this.io.change(result.settings);this.$('lensAutoStatus').textContent=result.message;
  }catch(e){this.io.error(e.message);}finally{this.busy=false;this.sync();}
 }
 async estimate(){if(this.busy||!this.io.current())return;this.busy=true;this.sync();const f=this.io.current(),signature=JSON.stringify(f.settings);
  try{const result=await this.io.estimate(f);if(this.io.current()!==f||JSON.stringify(f.settings)!==signature)throw Error('编辑已改变，未应用旧色差估计');if(result.applied)this.io.change(result.settings);this.$('lensAutoStatus').textContent=result.message;this.io.error(result.message);
  }catch(e){this.io.error(e.message);}finally{this.busy=false;this.sync();}
 }
}
