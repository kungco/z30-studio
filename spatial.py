"""Float32 spatial edits: analytic masks, edge-aware denoise and manual optics."""
import math
import numpy as np

LIMITS = dict(denoise_luma=(0,100),denoise_chroma=(0,100),lens_k1=(-.3,.3),lens_k2=(-.2,.2),
              lens_red=(-10,10),lens_blue=(-10,10),vignette=(0,100))
DEFAULTS = {key:0 for key in LIMITS} | {'masks':[]}
LUMA = np.array([.2126,.7152,.0722],np.float32)


def masks(value):
    if not isinstance(value,list) or len(value)>8:
        raise ValueError('最多支持 8 个局部蒙版')
    result=[];total_points=0
    for m in value:
        if not isinstance(m,dict) or m.get('type') not in ('linear','radial','brush'):
            raise ValueError('蒙版类型无效')
        item=dict(type=m['type'],enabled=bool(m.get('enabled',True)),invert=bool(m.get('invert',False)))
        bounds=dict(x0=(0,1,.2),y0=(0,1,.2),x1=(0,1,.8),y1=(0,1,.8),cx=(0,1,.5),cy=(0,1,.5),
                    rx=(.01,1,.25),ry=(.01,1,.25),radius=(.005,.3,.06),feather=(.01,1,.6),
                    opacity=(0,1,1),exposure=(-2,2,0),shadows=(-100,100,0),temperature=(-60,60,0),saturation=(-100,100,0))
        for key,(lo,hi,default) in bounds.items():
            v=m.get(key,default)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
                raise ValueError('蒙版参数超出范围：'+key)
            item[key]=float(v)
        points=m.get('points',[])
        if not isinstance(points,list):raise ValueError('画笔轨迹无效')
        clean=[]
        for point in points:
            if not isinstance(point,list) or len(point)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in point):
                raise ValueError('画笔坐标无效')
            clean.append([round(float(v),5) for v in point])
        total_points+=len(clean)
        if total_points>512:raise ValueError('画笔轨迹最多 512 个点，请分组编辑')
        breaks=m.get('breaks',[])
        if not isinstance(breaks,list) or any(isinstance(i,bool) or not isinstance(i,int) or not 0<i<len(clean) for i in breaks) or breaks!=sorted(set(breaks)):
            raise ValueError('画笔分段索引无效')
        item['points']=clean;item['breaks']=list(breaks);result.append(item)
    return result


def mask_field(m,w,h,top,bottom):
    x=(np.arange(w,dtype=np.float32)+.5)[None,:]/w
    y=(np.arange(top,bottom,dtype=np.float32)+.5)[:,None]/h
    if m['type']=='linear':
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
        weight=mask_field(m,w,h,top,bottom)
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
    if not any(p.get(k,0) for k in ('lens_k1','lens_k2','lens_red','lens_blue','vignette')):return arr
    h,w=arr.shape[:2];out=np.empty_like(arr);cx=(w-1)/2;cy=(h-1)/2;scale=max(cx,cy,1)
    xx=(np.arange(w,dtype=np.float32)[None,:]-cx)/scale
    for top in range(0,h,128):
        bottom=min(h,top+128);yy=(np.arange(top,bottom,dtype=np.float32)[:,None]-cy)/scale
        r2=xx*xx+yy*yy;radial=1+p.get('lens_k1',0)*r2+p.get('lens_k2',0)*r2*r2
        for c,offset in enumerate((p.get('lens_red',0),0,p.get('lens_blue',0))):
            factor=radial*(1+offset/1000)
            out[top:bottom,:,c]=_sample(arr[...,c],cx+xx*factor*scale,cy+yy*factor*scale)
        if p.get('vignette',0):out[top:bottom]*=np.exp2(np.clip(r2,0,2)*p['vignette']/100)[...,None]
    return out


def denoise(arr,p,inplace=False):
    l=p.get('denoise_luma',0)/100;c=p.get('denoise_chroma',0)/100
    if not l and not c:return arr
    out=arr if inplace else np.empty_like(arr);h,w=arr.shape[:2];tail=None
    offsets=[(-2,0),(2,0),(0,-2),(0,2),(-1,-1),(-1,1),(1,-1),(1,1),(0,0)]
    for top in range(0,h,128):
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
