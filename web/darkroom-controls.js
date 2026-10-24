'use strict';
const DARKROOM_DEFAULTS={curves:{rgb:[[0,0],[1,1]],r:[[0,0],[1,1]],g:[[0,0],[1,1]],b:[[0,0],[1,1]]},hsl:{},grading:{},wb_mode:'legacy',kelvin:6500,wb_gains:[1,1,1],wb_calibration:'',straighten:0,crop_rect:[0,0,1,1],clarity:0,texture:0,dehaze:0};
class DarkroomControls {
 constructor(io){
  this.io=io;this.p=DARKROOM_DEFAULTS;this.point=0;this.channel='rgb';
  document.getElementById('darkroomMount').innerHTML=`
   <details class="advanced-panel"><summary>曲线 · RGB / 单通道</summary><select id="curveChannel" aria-label="曲线通道"><option value="rgb">RGB 总曲线</option><option value="r">红通道</option><option value="g">绿通道</option><option value="b">蓝通道</option></select><canvas id="curveCanvas" width="260" height="180" aria-label="曲线控制图，点击添加或拖动点，双击删除点"></canvas><p class="controlnote">点击增加点，拖动调整；双击删除中间点。曲线保持平滑且不倒置亮度。</p><label>所选点输入 %<input id="curveX" type="number" min="0" max="100" step=".1"></label><label>所选点输出 %<input id="curveY" type="number" min="0" max="100" step=".1"></label><button id="curveReset">重置此通道</button></details>
   <details class="advanced-panel"><summary>HSL 分色 · 颜色分级</summary><select id="hslColor" aria-label="HSL 颜色"><option value="red">红</option><option value="orange">橙</option><option value="yellow">黄</option><option value="green">绿</option><option value="aqua">青</option><option value="blue">蓝</option><option value="purple">紫</option><option value="magenta">洋红</option></select><div id="hslControls"></div><select id="gradeBand" aria-label="颜色分级区域"><option value="shadows">阴影</option><option value="midtones">中间调</option><option value="highlights">高光</option></select><div id="gradeControls"></div></details>
   <details class="advanced-panel"><summary>白平衡吸管 · Kelvin / 灰卡</summary><select id="wbMode" aria-label="精细白平衡方式"><option value="legacy">使用上方相机 / 自动白平衡</option><option value="kelvin">Kelvin 光源校正</option><option value="custom">灰卡取样 / 校准增益</option></select><label>光源色温 K<input id="kelvin" type="number" min="2000" max="15000" step="1" value="6500"></label><button id="wbPick">吸管：点击照片中的灰卡</button><label>校准名称<input id="wbCalibration" maxlength="100" placeholder="例如：室内灰卡"></label><p id="wbSampleInfo" class="controlnote">Kelvin 为相对 6500 K 的光源近似校正；不是尼康专用传感器标定。JPEG 使用已编码图像作为起点。</p></details>
   <details class="advanced-panel"><summary>自由裁剪 · 水平拉直 · 构图网格</summary><button id="freeCrop">绘制自由裁剪框</button><label>拉直角度 °<input id="straighten" type="range" min="-45" max="45" step=".1" value="0"><output id="straightenValue">0</output></label><label><input id="compositionGrid" type="checkbox"> 三分网格（仅显示，不导出）</label></details>
   <details class="advanced-panel"><summary>纹理 · 清晰度 · 去雾</summary><div id="textureControls"></div><p class="controlnote">去雾为可控的近似透射对比调整，不重建被遮挡细节。</p></details>`;
  this.$=id=>document.getElementById(id);
  const ranges=(target,items,change)=>{for(const [key,label,min,max] of items){const row=document.createElement('label');row.textContent=label;const input=document.createElement('input');Object.assign(input,{id:target+'-'+key,type:'range',min,max,step:1});input.setAttribute('aria-label',label);const out=document.createElement('output');out.id=input.id+'-value';input.oninput=()=>change(key,Number(input.value));row.append(input,out);this.$(target).append(row);}};
  ranges('hslControls',[['hue','分色色相',-45,45],['saturation','分色饱和度',-100,100],['luminance','分色明度',-100,100]],(key,value)=>{const name=this.$('hslColor').value;io.change({hsl:{...this.p.hsl,[name]:{...this.p.hsl[name],[key]:value}}});});
  ranges('gradeControls',[['hue','分级色相',0,360],['saturation','分级饱和度',0,100],['luminance','分级明度',-100,100],['balance','分级平衡',-100,100]],(key,value)=>{const band=this.$('gradeBand').value;io.change({grading:key==='balance'?{...this.p.grading,balance:value}:{...this.p.grading,[band]:{...this.p.grading[band],[key]:value}}});});
  ranges('textureControls',[['clarity','清晰度',-100,100],['texture','纹理',-100,100],['dehaze','去雾',-100,100]],(key,value)=>io.change({[key]:value}));
  this.$('hslColor').onchange=this.$('gradeBand').onchange=()=>this.sync(this.p,io.locked());
  this.$('curveChannel').onchange=()=>{this.channel=this.$('curveChannel').value;this.point=0;this.drawCurve();};
  this.$('curveReset').onclick=()=>io.change({curves:{...this.p.curves,[this.channel]:[[0,0],[1,1]]}});
  const canvas=this.$('curveCanvas');
  const pos=e=>{const r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,1-(e.clientY-r.top)/r.height))];};
  canvas.onpointerdown=e=>{if(io.locked())return;e.preventDefault();canvas.setPointerCapture(e.pointerId);const [x,y]=pos(e);const points=this.points();let closest=points.findIndex(p=>Math.hypot(p[0]-x,p[1]-y)<.06);
   if(closest<0){if(points.length>=33)return;closest=points.findIndex(p=>p[0]>x);if(closest<1)return;points.splice(closest,0,[x,Math.max(points[closest-1][1],Math.min(points[closest][1],y))]);this.commit(points);}this.point=closest;this.dragging=true;this.drawCurve();};
  canvas.onpointermove=e=>{if(!this.dragging)return;const [x,y]=pos(e);this.movePoint(x,y);};
  canvas.onpointerup=canvas.onpointercancel=()=>this.dragging=false;
  canvas.ondblclick=()=>{const points=this.points();if(this.point>0&&this.point<points.length-1){points.splice(this.point,1);this.point--;this.commit(points);}};
  this.$('curveX').oninput=()=>{if(this.$('curveX').value!=='')this.movePoint(Number(this.$('curveX').value)/100,this.points()[this.point][1]);};
  this.$('curveY').oninput=()=>{if(this.$('curveY').value!=='')this.movePoint(this.points()[this.point][0],Number(this.$('curveY').value)/100);};
  this.$('wbMode').onchange=()=>io.change({wb_mode:this.$('wbMode').value});
  this.$('kelvin').onchange=()=>{const k=Number(this.$('kelvin').value);if(Number.isFinite(k)&&k>=2000&&k<=15000)io.change({kelvin:k,wb_mode:'kelvin'});else io.error('Kelvin 需为 2000–15000');};
  this.$('wbCalibration').onchange=()=>io.change({wb_calibration:this.$('wbCalibration').value});
  this.$('wbPick').onclick=()=>io.sample();this.$('freeCrop').onclick=()=>io.crop();
  this.$('straighten').oninput=()=>io.change({straighten:Number(this.$('straighten').value)});
  this.$('compositionGrid').onchange=()=>io.grid(this.$('compositionGrid').checked);
 }
 points(){return JSON.parse(JSON.stringify(this.p.curves?.[this.channel]||[[0,0],[1,1]]));}
 commit(points){this.io.change({curves:{...this.p.curves,[this.channel]:points}});}
 movePoint(x,y){if(this.io.locked()||!Number.isFinite(x)||!Number.isFinite(y))return;const points=this.points(),i=Math.min(this.point,points.length-1);const lo=points[Math.max(0,i-1)],hi=points[Math.min(points.length-1,i+1)];points[i]=[i===0?0:i===points.length-1?1:Math.max(lo[0]+.002,Math.min(hi[0]-.002,x)),Math.max(i===0?0:lo[1],Math.min(i===points.length-1?1:hi[1],y))];this.commit(points);}
 drawCurve(){const c=this.$('curveCanvas'),ctx=c.getContext('2d'),points=this.points();this.point=Math.min(this.point,points.length-1);ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle='#444';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){ctx.beginPath();ctx.moveTo(i*c.width/4,0);ctx.lineTo(i*c.width/4,c.height);ctx.moveTo(0,i*c.height/4);ctx.lineTo(c.width,i*c.height/4);ctx.stroke();}
  ctx.strokeStyle={r:'#e77979',g:'#8ac98a',b:'#8aaee4',rgb:'#efcf79'}[this.channel];ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p[0]*c.width,(1-p[1])*c.height):ctx.moveTo(p[0]*c.width,(1-p[1])*c.height));ctx.stroke();points.forEach((p,i)=>{ctx.beginPath();ctx.arc(p[0]*c.width,(1-p[1])*c.height,i===this.point?5:3,0,Math.PI*2);ctx.fillStyle=ctx.strokeStyle;ctx.fill();});this.$('curveX').value=(points[this.point][0]*100).toFixed(1);this.$('curveY').value=(points[this.point][1]*100).toFixed(1);}
 sync(p,locked){this.p={...DARKROOM_DEFAULTS,...p};this.$('darkroomMount').querySelectorAll('button,input,select').forEach(e=>e.disabled=locked);
  this.$('wbMode').value=this.p.wb_mode;this.$('kelvin').value=this.p.kelvin;this.$('wbCalibration').value=this.p.wb_calibration;
  this.$('straighten').value=this.p.straighten;this.$('straightenValue').textContent=this.p.straighten.toFixed(1);
  for(const [group,data] of [['hslControls',this.p.hsl[this.$('hslColor').value]||{}],['gradeControls',this.p.grading[this.$('gradeBand').value]||{}],['textureControls',this.p]]){
   for(const input of this.$(group).querySelectorAll('input')){const key=input.id.slice(group.length+1),v=key==='balance'?this.p.grading.balance||0:data[key]||0;input.value=v;this.$(input.id+'-value').textContent=v;}}
  this.drawCurve();}
}
