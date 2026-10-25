"""Float32 spatial edits: analytic masks, edge-aware denoise and manual optics."""
import native_cache as frame_memory
import math
import base64
import io
from functools import lru_cache
import numpy as np
from PIL import Image

LIMITS = dict(denoise_luma=(0,100),denoise_chroma=(0,100),lens_k1=(-.3,.3),lens_k2=(-.2,.2),
              lens_red=(-10,10),lens_blue=(-10,10),vignette=(0,100),skin_smooth=(0,100))
DEFAULTS = {key:0 for key in LIMITS} | {'masks':[], 'repairs':[], 'skin_mask':''}
LUMA = np.array([.2126,.7152,.0722],np.float32)


def masks(value):
    if not isinstance(value,list) or len(value)>32:
        raise ValueError('最多支持 32 个局部蒙版')
    result=[];total_points=0
    for m in value:
        if not isinstance(m,dict) or m.get('type') not in ('linear','radial','brush','luminance','color','semantic'):
            raise ValueError('蒙版类型无效')
        item=dict(type=m['type'],enabled=bool(m.get('enabled',True)),invert=bool(m.get('invert',False)))
        bounds=dict(x0=(0,1,.2),y0=(0,1,.2),x1=(0,1,.8),y1=(0,1,.8),cx=(0,1,.5),cy=(0,1,.5),
                    rx=(.01,1,.25),ry=(.01,1,.25),radius=(.005,.3,.06),feather=(.01,1,.6),
                    opacity=(0,1,1),exposure=(-2,2,0),shadows=(-100,100,0),temperature=(-60,60,0),saturation=(-100,100,0),
                    range_min=(0,1,0),range_max=(0,1,1),range_softness=(.005,.5,.08),color_distance=(.01,1,.18))
        for key,(lo,hi,default) in bounds.items():
            v=m.get(key,default)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
                raise ValueError('蒙版参数超出范围：'+key)
            item[key]=float(v)
        if item['range_min']>item['range_max']:raise ValueError('亮度范围下限不能大于上限')
        color=m.get('range_color',[.5,.5,.5])
        if not isinstance(color,list) or len(color)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in color):
            raise ValueError('颜色范围取样无效')
        item['range_color']=list(color)
        if item['type']=='semantic':
            encoded=m.get('bitmap','')
            bitmap_field(encoded)  # Validate bounded PNG, never deserialize objects.
            item['bitmap']=encoded;item['label']=str(m.get('label','AI 区域'))[:64]
        points=m.get('points',[])
        if not isinstance(points,list):raise ValueError('画笔轨迹无效')
        clean=[]
        for point in points:
            if not isinstance(point,list) or len(point)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in point):
                raise ValueError('画笔坐标无效')
            clean.append([round(float(v),5) for v in point])
        total_points+=len(clean)
        if total_points>8192:raise ValueError('画笔轨迹最多 8192 个点，请分组编辑')
        breaks=m.get('breaks',[])
        if not isinstance(breaks,list) or any(isinstance(i,bool) or not isinstance(i,int) or not 0<i<len(clean) for i in breaks) or breaks!=sorted(set(breaks)):
            raise ValueError('画笔分段索引无效')
        item['points']=clean;item['breaks']=list(breaks);result.append(item)
    return result


@lru_cache(maxsize=32)
def bitmap_field(encoded):
    if not isinstance(encoded,str) or not 0<len(encoded)<=65536:raise ValueError('AI 蒙版数据为空或过大')
    try:
        with Image.open(io.BytesIO(base64.b64decode(encoded,validate=True))) as image:
            if image.format!='PNG' or image.mode!='L' or not 1<=image.width<=512 or not 1<=image.height<=512:
                raise ValueError('AI 蒙版需为有界灰度 PNG')
            out=np.asarray(image,dtype=np.float32)/255;out.setflags(write=False);return out
    except (OSError,ValueError,TypeError,Image.DecompressionBombError) as exc:raise ValueError('AI 蒙版 PNG 无效') from exc


def mask_field(m,w,h,top,bottom,arr=None):
    x=(np.arange(w,dtype=np.float32)+.5)[None,:]/w
    y=(np.arange(top,bottom,dtype=np.float32)+.5)[:,None]/h
    if m['type']=='semantic':
        bitmap=bitmap_field(m['bitmap']);mh,mw=bitmap.shape
        field=_sample(bitmap,np.clip(x*mw-.5,0,mw-1),np.clip(y*mh-.5,0,mh-1))
        field=np.clip((field-(1-m['feather'])*.25)/max(m['feather'],.01),0,1)
    elif m['type'] in ('luminance','color'):
        if arr is None:raise ValueError('范围蒙版需要原片像素')
        soft=m['range_softness']
        if m['type']=='luminance':
            values=np.clip(arr@LUMA,0,1)**np.float32(1/2.2)
            low=np.clip((values-m['range_min']+soft)/soft,0,1);high=np.clip((m['range_max']+soft-values)/soft,0,1)
            field=(low*low*(3-2*low))*(high*high*(3-2*high))
        else:
            from processing import rgb_to_oklab
            from imaging import srgb_to_linear
            lab=rgb_to_oklab(arr);target=rgb_to_oklab(srgb_to_linear(np.asarray(m['range_color'],np.float32)))
            distance=np.sqrt(np.sum((lab-target)**2,axis=2));field=np.clip((m['color_distance']+soft-distance)/soft,0,1)
            field=field*field*(3-2*field)
    elif m['type']=='linear':
        dx=m['x1']-m['x0'];dy=m['y1']-m['y0'];length=dx*dx+dy*dy
        field=np.clip(((x-m['x0'])*dx+(y-m['y0'])*dy)/max(length,1e-6),0,1)
        field=field*field*(3-2*field)
    elif m['type']=='radial':
        distance=np.sqrt(((x-m['cx'])/m['rx'])**2+((y-m['cy'])/m['ry'])**2)
        field=np.clip((1-distance)/m['feather'],0,1)
        field=field*field*(3-2*field)
    else:
        field=np.zeros((bottom-top,w),np.float32)
        points=m['points'];radius=m['radius']*min(w,h)
        starts={0,*m.get('breaks',[])}
        segments=[(p,p) for i,p in enumerate(points) if i in starts]+[(points[i-1],p) for i,p in enumerate(points) if i and i not in starts]
        for a,b in segments:
            ax,ay=a[0]*w,a[1]*h;bx,by=b[0]*w,b[1]*h
            left=max(0,int(min(ax,bx)-radius));right=min(w,int(max(ax,bx)+radius)+1)
            low=max(top,int(min(ay,by)-radius));high=min(bottom,int(max(ay,by)+radius)+1)
            if left>=right or low>=high:continue
            xx=np.arange(left,right,dtype=np.float32)[None,:]+.5
            yy=np.arange(low,high,dtype=np.float32)[:,None]+.5
            dx=bx-ax;dy=by-ay
            t=np.clip(((xx-ax)*dx+(yy-ay)*dy)/max(dx*dx+dy*dy,1e-6),0,1)
            d=np.sqrt((xx-ax-t*dx)**2+(yy-ay-t*dy)**2)
            cover=np.clip((radius-d)/(radius*m['feather']),0,1)
            region=field[low-top:high-top,left:right]
            np.maximum(region,cover*cover*(3-2*cover),out=region)
    if m['invert']:field=1-field
    return field*np.float32(m['opacity'])


def local_chunk(arr,p,w,h,top,bottom):
    active=[m for m in p.get('masks',[]) if m['enabled'] and m['opacity'] and any(m[k] for k in ('exposure','shadows','temperature','saturation'))]
    if not active:return arr
    out=arr.copy()
    for m in active:
        weight=mask_field(m,w,h,top,bottom,arr)
        y=np.maximum(out@LUMA,0)
        gain=np.exp2(weight*(m['exposure']+m['shadows']/100*np.exp(-y/.18)))
        out*=gain[...,None]
        if m['temperature']:
            gains=np.stack(np.broadcast_arrays(np.exp2(weight*m['temperature']/180),np.ones_like(weight),np.exp2(-weight*m['temperature']/180)),axis=2)
            gains/=np.maximum(gains@LUMA,1e-8)[...,None];out*=gains
        if m['saturation']:
            y=out@LUMA;out=y[...,None]+(out-y[...,None])*(1+weight[...,None]*m['saturation']/100)
    return out


def _sample(a,x,y):
    h,w=a.shape[:2];valid=(x>=0)&(y>=0)&(x<=w-1)&(y<=h-1)
    xx=np.clip(x,0,w-1);yy=np.clip(y,0,h-1)
    x0=xx.astype(np.int32);y0=yy.astype(np.int32);x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1)
    fx=xx-x0.astype(np.float32);fy=yy-y0.astype(np.float32)
    out=(a[y0,x0]*(1-fx)+a[y0,x1]*fx)*(1-fy)+(a[y1,x0]*(1-fx)+a[y1,x1]*fx)*fy
    return np.where(valid,out,0).astype(np.float32)


def optical(arr,p):
    if p.get('lens_auto'):
        from lens_tools import correct
        arr=correct(arr,p,_sample)
        frame_memory.release_input(arr)
    if not any(p.get(k,0) for k in ('lens_k1','lens_k2','lens_red','lens_blue','vignette')):return arr
    h,w=arr.shape[:2];out=frame_memory.allocate(arr.shape,arr.dtype);cx=(w-1)/2;cy=(h-1)/2;scale=max(cx,cy,1)
    xx=(np.arange(w,dtype=np.float32)[None,:]-cx)/scale
    for top in range(0,h,128):
        frame_memory.checkpoint()
        bottom=min(h,top+128);yy=(np.arange(top,bottom,dtype=np.float32)[:,None]-cy)/scale
        r2=xx*xx+yy*yy;radial=1+p.get('lens_k1',0)*r2+p.get('lens_k2',0)*r2*r2
        for c,offset in enumerate((p.get('lens_red',0),0,p.get('lens_blue',0))):
            factor=radial*(1+offset/1000)
            out[top:bottom,:,c]=_sample(arr[...,c],cx+xx*factor*scale,cy+yy*factor*scale)
        if p.get('vignette',0):out[top:bottom]*=np.exp2(np.clip(r2,0,2)*p['vignette']/100)[...,None]
    frame_memory.release_input(out)
    return out


def denoise(arr,p,inplace=False):
    l=p.get('denoise_luma',0)/100;c=p.get('denoise_chroma',0)/100
    if not l and not c:return arr
    out=arr if inplace else frame_memory.allocate(arr.shape,arr.dtype);h,w=arr.shape[:2];tail=None
    offsets=[(-2,0),(2,0),(0,-2),(0,2),(-1,-1),(-1,1),(1,-1),(1,1),(0,0)]
    for top in range(0,h,128):
        frame_memory.checkpoint()
        bottom=min(h,top+128);lo=max(0,top-2);hi=min(h,bottom+2)
        source=arr[lo:hi].copy() if inplace else arr[lo:hi]
        if inplace and tail is not None:source[:len(tail)]=tail
        tail=arr[max(top,bottom-2):bottom].copy() if inplace else None
        padded=np.pad(source,((2,2),(2,2),(0,0)),mode='edge')
        start=top-lo+2;center=arr[top:bottom];y=center@LUMA
        sigma=.006+.065*max(l,c);total=np.zeros_like(center);weight_sum=np.zeros_like(y)
        for dy,dx in offsets:
            neighbour=padded[start+dy:start+dy+bottom-top,2+dx:2+dx+w]
            difference=(neighbour@LUMA-y)
            weight=np.exp(-difference*difference/(2*sigma*sigma))*math.exp(-(dx*dx+dy*dy)/5)
            total+=neighbour*weight[...,None];weight_sum+=weight
        smooth=total/weight_sum[...,None];smooth_y=smooth@LUMA
        target_y=y+(smooth_y-y)*l
        chroma=(center-y[...,None])*(1-c)+(smooth-smooth_y[...,None])*c
        out[top:bottom]=np.maximum(target_y[...,None]+chroma,0)
    return out


def prepare(arr,p):
    corrected=optical(arr,p)
    return denoise(corrected,p,inplace=corrected is not arr)


def repairs(value):
    if not isinstance(value,list) or len(value)>256:raise ValueError('最多支持 256 个修复点')
    result=[]
    for item in value:
        if not isinstance(item,dict) or item.get('mode') not in ('heal','clone'):raise ValueError('修复方式无效')
        out={'mode':item['mode']}
        for key,(lo,hi,default) in dict(x=(0,1,.5),y=(0,1,.5),sx=(0,1,.6),sy=(0,1,.5),radius=(.002,.2,.015),opacity=(0,1,1),feather=(.01,1,.5)).items():
            v=item.get(key,default)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('修复坐标或强度无效')
            out[key]=float(v)
        result.append(out)
    return result


def choose_donor(arr,x,y,radius):
    """Choose once on preview; stored normalized donor makes export deterministic."""
    h,w=arr.shape[:2];r=max(1,radius*min(h,w));cx=x*(w-1);cy=y*(h-1)
    def sample(px,py):
        yy=np.linspace(py-r,py+r,16,dtype=np.float32)[:,None]
        xx=np.linspace(px-r,px+r,16,dtype=np.float32)[None,:]
        return np.stack([_sample(arr[...,c],xx,yy) for c in range(3)],axis=2)
    reference=sample(cx,cy);ring=np.hypot(*np.mgrid[-1:1:16j,-1:1:16j])>.8
    candidates=[]
    for dx,dy in ((3,0),(-3,0),(0,3),(0,-3),(2.2,2.2),(-2.2,-2.2)):
        sx,sy=cx+dx*r,cy+dy*r
        if not r<=sx<w-r or not r<=sy<h-r:continue
        patch=sample(sx,sy);score=float(np.mean((patch[ring]-reference[ring])**2))
        candidates.append((score,sx/max(w-1,1),sy/max(h-1,1)))
    if not candidates:raise ValueError('此处没有足够的邻近取样区域，请缩小修复点或使用克隆手动取样')
    _,sx,sy=min(candidates);return sx,sy


def retouch(arr,p):
    """Float clone / mean-matched heal, then conservative portrait skin smoothing."""
    edits=p.get('repairs',[]);amount=p.get('skin_smooth',0)
    if not edits and not amount:return arr
    h,w=arr.shape[:2];out=frame_memory.copy_array(arr) if edits else arr
    for edit in edits:
        radius=max(1,edit['radius']*min(w,h));cx=edit['x']*(w-1);cy=edit['y']*(h-1)
        left=max(0,int(cx-radius));right=min(w,int(cx+radius)+2);top=max(0,int(cy-radius));bottom=min(h,int(cy+radius)+2)
        xx=np.arange(left,right,dtype=np.float32)[None,:]-cx;yy=np.arange(top,bottom,dtype=np.float32)[:,None]-cy
        distance=np.sqrt(xx*xx+yy*yy)/radius
        weight=np.clip((1-distance)/edit['feather'],0,1);weight=weight*weight*(3-2*weight)*edit['opacity']
        sx=edit['sx']*(w-1)+xx;sy=edit['sy']*(h-1)+yy
        valid=(sx>=0)&(sy>=0)&(sx<=w-1)&(sy<=h-1);weight*=valid
        donor=np.stack([_sample(arr[...,c],sx,sy) for c in range(3)],axis=2)
        destination=out[top:bottom,left:right]
        if edit['mode']=='heal':
            ring=(distance>.7)&(distance<1.1)&valid
            if ring.any():donor=np.maximum(donor+(destination[ring].mean(axis=0)-donor[ring].mean(axis=0)),0)
        destination[:]=destination*(1-weight[...,None])+donor*weight[...,None]
    if amount:
        encoded=p.get('skin_mask','')
        if not encoded:raise ValueError('请先生成本地人像区域，再启用肤色柔化')
        from processing import rgb_to_oklab
        smooth=denoise(out,{'denoise_luma':amount*.35,'denoise_chroma':amount*.6})
        for top in range(0,h,128):
            frame_memory.checkpoint()
            bottom=min(h,top+128);region=out[top:bottom];lab=rgb_to_oklab(region)
            hue=np.arctan2(lab[...,2],lab[...,1]);chroma=np.hypot(lab[...,1],lab[...,2])
            # Broad warm-colour selection within an actual person semantic mask.
            # This is not facial landmarks, diagnosis, identity or precise skin parsing.
            warmth=np.clip((hue-.15)/.25,0,1)*np.clip((1.5-hue)/.3,0,1)
            warmth*=np.clip((chroma-.015)/.025,0,1)*np.clip((.3-chroma)/.08,0,1)
            person=mask_field(dict(type='semantic',bitmap=encoded,feather=1.,opacity=1.,invert=False),w,h,top,bottom)
            weight=(warmth*person*np.float32(amount/100))[...,None]
            smooth[top:bottom]=region*(1-weight)+smooth[top:bottom]*weight
        out=smooth
    return out
