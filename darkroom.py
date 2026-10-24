"""Bounded, non-destructive manual darkroom controls; float32 until final encode."""
from functools import lru_cache
import math
import numpy as np
from imaging import srgb_to_linear

CHANNELS = ('rgb','r','g','b')
COLORS = ('red','orange','yellow','green','aqua','blue','purple','magenta')
DEFAULTS = dict(curves={k:[[0,0],[1,1]] for k in CHANNELS}, hsl={}, grading={},
                wb_mode='legacy', kelvin=6500, wb_gains=[1,1,1], wb_calibration='',
                straighten=0, crop_rect=[0,0,1,1], clarity=0, texture=0, dehaze=0)


def number(value, lo, hi):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:
        raise ValueError('暗房参数超出范围')
    return float(value)


def normalize(data):
    out={}
    out['wb_mode']=data.get('wb_mode','legacy')
    if out['wb_mode'] not in ('legacy','kelvin','custom'):raise ValueError('白平衡方式无效')
    out['kelvin']=number(data.get('kelvin',6500),2000,15000)
    gains=data.get('wb_gains',[1,1,1])
    if not isinstance(gains,list) or len(gains)!=3:raise ValueError('取样白平衡增益无效')
    out['wb_gains']=[number(v,.125,8) for v in gains]
    out['wb_calibration']=str(data.get('wb_calibration',''))[:100]
    out['straighten']=number(data.get('straighten',0),-45,45)
    rect=data.get('crop_rect',[0,0,1,1])
    if not isinstance(rect,list) or len(rect)!=4:raise ValueError('自由裁剪框无效')
    x,y,w,h=[number(v,0,1) for v in rect]
    if min(w,h)<.02 or x+w>1.000001 or y+h>1.000001:raise ValueError('自由裁剪框越界或过小')
    out['crop_rect']=[x,y,w,h]
    for k in ('clarity','texture','dehaze'):out[k]=number(data.get(k,0),-100,100)
    curves=data.get('curves',{})
    if not isinstance(curves,dict) or set(curves)-set(CHANNELS):raise ValueError('曲线通道无效')
    out['curves']={}
    for k in CHANNELS:
        points=curves.get(k,[[0,0],[1,1]])
        if not isinstance(points,list) or not 2<=len(points)<=33:raise ValueError('曲线需要 2–33 个控制点')
        clean=[]
        for point in points:
            if not isinstance(point,list) or len(point)!=2:raise ValueError('曲线控制点无效')
            clean.append([number(v,0,1) for v in point])
        if clean[0][0]!=0 or clean[-1][0]!=1 or any(b[0]-a[0]<.001 or b[1]<a[1] for a,b in zip(clean,clean[1:])):
            raise ValueError('曲线横坐标需递增且覆盖 0–1，亮度不能倒置')
        out['curves'][k]=clean
    hsl=data.get('hsl',{});grade=data.get('grading',{})
    if not isinstance(hsl,dict) or set(hsl)-set(COLORS):raise ValueError('HSL 颜色分区无效')
    out['hsl']={}
    for k,v in hsl.items():
        if not isinstance(v,dict) or set(v)-{'hue','saturation','luminance'}:raise ValueError('HSL 参数无效')
        out['hsl'][k]={n:number(v.get(n,0),-45 if n=='hue' else -100,45 if n=='hue' else 100) for n in ('hue','saturation','luminance')}
    if not isinstance(grade,dict) or set(grade)-{'shadows','midtones','highlights','balance'}:raise ValueError('颜色分级无效')
    out['grading']={}
    for band in ('shadows','midtones','highlights'):
        if band not in grade:continue
        v=grade[band]
        if not isinstance(v,dict):raise ValueError('颜色分级参数无效')
        out['grading'][band]=dict(hue=number(v.get('hue',0),0,360),saturation=number(v.get('saturation',0),0,100),
                                  luminance=number(v.get('luminance',0),-100,100))
    if 'balance' in grade:out['grading']['balance']=number(grade['balance'],-100,100)
    return out


@lru_cache(maxsize=128)
def curve_table(points):
    """Monotone cubic Hermite interpolation without an extra SciPy dependency."""
    a=np.asarray(points,dtype=np.float64);x,y=a[:,0],a[:,1]
    h=np.diff(x);d=np.diff(y)/h;m=np.zeros(len(x),np.float64)
    if len(x)==2:m[:]=d[0]
    else:
        for i in range(1,len(x)-1):
            if d[i-1]*d[i]>0:
                w1=2*h[i]+h[i-1];w2=h[i]+2*h[i-1]
                m[i]=(w1+w2)/(w1/d[i-1]+w2/d[i])
        for i,j,k in ((0,0,1),(-1,-1,-2)):
            slope=((2*h[j]+h[k])*d[j]-h[j]*d[k])/(h[j]+h[k])
            m[i]=max(0,min(3*d[j],slope))
    grid=np.linspace(0,1,4097);i=np.clip(np.searchsorted(x,grid,side='right')-1,0,len(x)-2)
    t=(grid-x[i])/h[i]
    table=(2*t**3-3*t*t+1)*y[i]+(t**3-2*t*t+t)*h[i]*m[i]+(-2*t**3+3*t*t)*y[i+1]+(t**3-t*t)*h[i]*m[i+1]
    table=np.clip(table,0,1).astype(np.float32);table.setflags(write=False);return table


def curve_apply(rgb, curves):
    out=rgb
    for k in CHANNELS:
        points=curves.get(k,[[0,0],[1,1]])
        if points==[[0,0],[1,1]]:continue
        if out is rgb:out=rgb.copy()
        values=out if k=='rgb' else out[...,CHANNELS.index(k)-1]
        table=curve_table(tuple(tuple(p) for p in points));pos=np.clip(values,0,1)*4096
        index=np.minimum(pos.astype(np.int32),4095);f=pos-index
        values[:]=table[index]*(1-f)+table[index+1]*f
    return out


def encode(rgb):
    out=np.maximum(rgb,0)**np.float32(1/2.4)*np.float32(1.055)-np.float32(.055)
    low=rgb<=.0031308;out[low]=rgb[low]*np.float32(12.92)
    return np.clip(out,0,1)


def color(rgb,p):
    curves=p.get('curves',{});hsl=p.get('hsl',{});grade=p.get('grading',{})
    out=curve_apply(rgb,curves)
    if not any(any(v.values()) for v in hsl.values()) and not any(v.get('saturation',0) or v.get('luminance',0) for v in grade.values() if isinstance(v,dict)):
        return out
    from processing import rgb_to_oklab,gamut_map
    import colour_management as colour
    space=p.get('output_space','srgb')
    lab=rgb_to_oklab(colour.to_working(colour.decode(out,space),space));hue=np.arctan2(lab[...,2],lab[...,1]);chroma=np.hypot(lab[...,1],lab[...,2])
    delta=np.zeros_like(hue);sat=np.ones_like(hue);light=np.zeros_like(hue)
    centers=np.deg2rad([29,60,110,142,195,264,300,328])
    for name,center in zip(COLORS,centers):
        if name not in hsl:continue
        a=hsl[name];dist=np.arctan2(np.sin(hue-center),np.cos(hue-center));weight=np.exp(-(dist/.38)**2)
        delta+=weight*np.deg2rad(a.get('hue',0));sat*=1+weight*a.get('saturation',0)/100
        light+=weight*a.get('luminance',0)/400
    lab[...,0]=np.clip(lab[...,0]+light,0,1)
    lab[...,1]=np.cos(hue+delta)*chroma*sat;lab[...,2]=np.sin(hue+delta)*chroma*sat
    l=np.clip(lab[...,0]+grade.get('balance',0)/500,0,1)
    weights={'shadows':(1-l)**2,'highlights':l*l,'midtones':2*l*(1-l)}
    for band,weight in weights.items():
        a=grade.get(band,{})
        if not a:continue
        angle=np.deg2rad(a.get('hue',0));amount=weight*a.get('saturation',0)/500
        lab[...,1]+=amount*np.cos(angle);lab[...,2]+=amount*np.sin(angle)
        lab[...,0]+=weight*a.get('luminance',0)/500
    return colour.encode(gamut_map(lab,space),space)


def kelvin_gains(temperature):
    """Kang 2002 Planckian xy approximation; neutral relative to 6500 K.

    This is an illuminant correction, not a Nikon sensor calibration or DCP.
    """
    def white(t):
        if t<=4000:x=-.2661239e9/t**3-.2343580e6/t**2+.8776956e3/t+.179910
        else:x=-3.0258469e9/t**3+2.1070379e6/t**2+.2226347e3/t+.240390
        if t<=2222:y=-1.1063814*x**3-1.34811020*x*x+2.18555832*x-.20219683
        elif t<=4000:y=-.9549476*x**3-1.37418593*x*x+2.09137015*x-.16748867
        else:y=3.0817580*x**3-5.87338670*x*x+3.75112997*x-.37001483
        xyz=np.array([x/y,1,(1-x-y)/y])
        return np.array([[3.2404542,-1.5371385,-.4985314],[-.969266,1.8760108,.041556],[.0556434,-.2040259,1.0572252]])@xyz
    gains=np.maximum(white(6500),.01)/np.maximum(white(float(temperature)),.01)
    gains/=gains@np.array([.2126,.7152,.0722]);return np.clip(gains,.125,8).astype(np.float32)


def wb_gains(p):
    if p.get('wb_mode')=='kelvin':return kelvin_gains(p.get('kelvin',6500))
    if p.get('wb_mode')=='custom':
        gains=np.array(p.get('wb_gains',[1,1,1]),np.float32);return gains/(gains@np.array([.2126,.7152,.0722],np.float32))
    return np.ones(3,np.float32)


def sample_neutral(arr,x,y):
    h,w=arr.shape[:2];cx=min(w-1,int(x*w));cy=min(h-1,int(y*h))
    patch=arr[max(0,cy-3):cy+4,max(0,cx-3):cx+4]
    median=np.median(patch,axis=(0,1))
    if median.min()<.005 or median.max()>.98:raise ValueError('取样过暗或接近通道上限，请选择未过曝的中性灰卡区域')
    gains=np.exp(np.mean(np.log(median)))/median
    if gains.min()<.125 or gains.max()>8:raise ValueError('取样色差过大，请重新选择灰卡')
    return gains.astype(float).tolist()


def geometry(arr,p):
    from spatial import _sample
    angle=p.get('straighten',0)
    if angle:
        h,w=arr.shape[:2];a=np.deg2rad(angle);c,s=np.cos(a),np.sin(a)
        zoom=max(abs(c)+abs(s)*h/w,abs(c)+abs(s)*w/h)
        oh,ow=max(1,int(h/zoom)),max(1,int(w/zoom));out=np.empty((oh,ow,3),np.float32)
        xx=(np.arange(ow,dtype=np.float32)[None,:]-(ow-1)/2)
        for top in range(0,oh,128):
            bottom=min(oh,top+128);yy=np.arange(top,bottom,dtype=np.float32)[:,None]-(oh-1)/2
            x=(w-1)/2+c*xx+s*yy;y=(h-1)/2-s*xx+c*yy
            for ch in range(3):out[top:bottom,:,ch]=_sample(arr[...,ch],x,y)
        arr=out
    if p.get('crop')=='free':
        x,y,cw,ch=p.get('crop_rect',[0,0,1,1]);h,w=arr.shape[:2]
        left,top=int(x*w),int(y*h);right=max(left+1,min(w,round((x+cw)*w)));bottom=max(top+1,min(h,round((y+ch)*h)))
        arr=arr[top:bottom,left:right]
    return arr


def box_blur(a,radius):
    if radius<1:return a
    padded=np.pad(a,((radius,radius),(radius,radius)),mode='edge')
    horizontal=np.cumsum(padded,axis=1,dtype=np.float64)
    horizontal=np.pad(horizontal,((0,0),(1,0)))
    horizontal=(horizontal[:,2*radius+1:]-horizontal[:,:-2*radius-1])/(2*radius+1)
    vertical=np.cumsum(horizontal,axis=0,dtype=np.float64);vertical=np.pad(vertical,((1,0),(0,0)))
    return (vertical[2*radius+1:]-vertical[:-2*radius-1])/(2*radius+1)


def detail(rgb,p,radius):
    if not p.get('clarity') and not p.get('texture'):return rgb
    from processing import LUMA,channel_bounds
    y=rgb@LUMA;target=y.copy()
    if p.get('clarity'):target+=(y-box_blur(y,radius))*np.float32(p['clarity']/100*.8)*(4*y*(1-y))
    if p.get('texture'):target+=(y-box_blur(y,2))*np.float32(p['texture']/100*.6)
    gain=np.divide(np.maximum(target,0),y,out=np.ones_like(y),where=y>1e-8)
    gain=np.minimum(gain,1/np.maximum(channel_bounds(rgb)[1],1e-8))
    return rgb*gain[...,None]
