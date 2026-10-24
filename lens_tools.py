"""Source-attributed Nikon Z calibrations and conservative image-based TCA.

Profile snapshots travel with edits; rendering never silently substitutes a newer
database. Pure NumPy strip warps avoid full-frame coordinate-map allocations.
"""
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import numpy as np
from PIL import Image
from app_runtime import resource_root

DEFAULTS=dict(lens_auto=False,lens_calibration=None,lens_tca=True,lens_fill=True,lens_vignette=False)


@lru_cache(maxsize=1)
def database():
    return json.loads((resource_root()/'data/lens-profiles.json').read_text(encoding='utf8'))


def key(value):
    return re.sub('[^a-z0-9]','',str(value).lower().replace('nikon corporation','').replace('nikon','').replace('\x00',''))


def metadata(path,camera=''):
    result=dict(lens_model='',focal_mm=None,aperture=None,sensor_crop=None,focus_m=None)
    try:
        with Image.open(path) as image:
            ex=image.getexif();sub=ex.get_ifd(34665)
            result['lens_model']=str(sub.get(42036,ex.get(42036,''))).replace('\x00','').strip()[:160]
            for tag,name in ((37386,'focal_mm'),(33437,'aperture'),(37382,'focus_m')):
                value=float(sub.get(tag,ex.get(tag,0)))
                if math.isfinite(value) and 0<value<=2000:result[name]=value
            equivalent=float(sub.get(41989,ex.get(41989,0)))
            if equivalent and result['focal_mm']:result['sensor_crop']=equivalent/result['focal_mm']
    except (OSError,ValueError,TypeError,ZeroDivisionError):pass
    for model,crop in database()['cameras'].items():
        if key(model)==key(camera):result['sensor_crop']=crop;break
    return result


def normalize(data):
    out={}
    for k,default in DEFAULTS.items():
        if k=='lens_calibration':continue
        value=data.get(k,default)
        if not isinstance(value,bool):raise ValueError('镜头开关无效')
        out[k]=value
    snapshot=data.get('lens_calibration')
    if snapshot is None:
        if out['lens_auto']:raise ValueError('请先匹配镜头配置')
        out['lens_calibration']=None;return out
    if not isinstance(snapshot,dict) or snapshot.get('model') not in ('ptlens','poly3','poly5'):
        raise ValueError('镜头校准模型不支持')
    from darkroom import number
    clean=dict(model=snapshot['model'],source_id=str(snapshot.get('source_id',''))[:160],revision=str(snapshot.get('revision',''))[:64])
    for name,default,lo,hi in (('crop',1.5,.5,10),('image_crop',1.531,.5,10),('aspect',1.5,1,3),('focal',50,1,2000),('aperture',8,.5,64),('distance',1000,.1,10000)):
        clean[name]=number(snapshot.get(name,default),lo,hi)
    for name,count,default,bound in (('coefficients',3,[0,0,0],5),('tca',6,[0,0,1,0,0,1],2),('vignetting',3,[0,0,0],10)):
        values=snapshot.get(name,default)
        if not isinstance(values,list) or len(values)!=count:raise ValueError('镜头系数数量无效')
        clean[name]=[number(v,-bound,bound) for v in values]
    if snapshot['model']=='ptlens' and 1-sum(clean['coefficients'])<.2:raise ValueError('镜头径向变换退化')
    if not .5<clean['tca'][2]<1.5 or not .5<clean['tca'][5]<1.5:raise ValueError('横向色差缩放异常')
    out['lens_calibration']=clean;return out


def interpolate(rows,focal,fields):
    rows=sorted(rows,key=lambda r:r['focal'])
    if not rows:return None,None
    low,high=rows[0]['focal'],rows[-1]['focal'];tolerance=max(.5,focal*.015)
    if focal<low-tolerance or focal>high+tolerance:raise ValueError('焦距超出该配置的校准范围，请确认镜头/焦距')
    focal=max(low,min(high,focal));a=max((r for r in rows if r['focal']<=focal),key=lambda r:r['focal'])
    b=min((r for r in rows if r['focal']>=focal),key=lambda r:r['focal'])
    if a['model']!=b['model']:raise ValueError('相邻焦距使用不同模型，暂不插值')
    t=0 if a['focal']==b['focal'] else (focal-a['focal'])/(b['focal']-a['focal'])
    return a['model'],[(a.get(k,default)*(1-t)+b.get(k,default)*t) for k,default in fields]


def select(meta,options):
    db=database();identifier=options.get('profile') or meta.get('lens_model','')
    matches=[p for p in db['profiles'] if key(p['id'])==key(identifier)]
    if len(matches)!=1:raise ValueError('没有唯一匹配的镜头配置，请手动选择；未套用猜测系数')
    profile=matches[0]
    def param(name,meta_name,default):
        value=options.get(name,meta.get(meta_name))
        if isinstance(value,bool):raise ValueError('镜头数值不能是开关')
        return float(default if value in (None,'') else value)
    focal=param('focal','focal_mm',0);crop=param('crop','sensor_crop',0)
    aperture=param('aperture','aperture',8);distance=param('distance','focus_m',1000)
    if not math.isfinite(focal) or focal<=0 or not math.isfinite(crop) or crop<=0:raise ValueError('缺少焦距或画幅信息，请手动填写')
    if not math.isfinite(aperture) or not .5<=aperture<=64 or not math.isfinite(distance) or not .1<=distance<=10000:
        raise ValueError('光圈或对焦距离超出范围')
    if crop<profile['crop']*.95:raise ValueError('配置基于更小画幅，请确认图像是否已经裁切并填写正确裁切系数')
    model,values=interpolate(profile['distortion'],focal,[('a',0),('b',0),('c',0)])
    if model=='poly3':_,values=interpolate(profile['distortion'],focal,[('k1',0),('k2',0),('k3',0)])
    if model=='poly5':_,values=interpolate(profile['distortion'],focal,[('k1',0),('k2',0),('k3',0)])
    _,tca=interpolate(profile['tca'],focal,[('br',0),('cr',0),('vr',1),('bb',0),('cb',0),('vb',1)]) if profile['tca'] else (None,None)
    if profile['tca'] and profile['tca'][0]['model']=='linear':
        _,linear=interpolate(profile['tca'],focal,[('kr',1),('kb',1)]);tca=[0,0,linear[0],0,0,linear[1]]
    vignette=[0,0,0]
    if profile['vignetting']:
        def dist(row):return abs(math.log(max(row['focal'],1)/focal))*2+abs(math.log(max(row['aperture'],.5)/aperture))+abs(1/max(row['distance'],.1)-1/distance)*.1
        nearest=sorted(profile['vignetting'],key=dist)[:4]
        weights=np.array([1/max(dist(r),1e-5)**2 for r in nearest]);weights/=weights.sum()
        vignette=[float(sum(weight*row.get(k,0) for weight,row in zip(weights,nearest))) for k in ('k1','k2','k3')]
    snapshot=dict(model=model,coefficients=values,tca=tca or [0,0,1,0,0,1],vignetting=vignette,
                  crop=profile['crop'],image_crop=crop,aspect=profile['aspect'],focal=focal,aperture=aperture,distance=distance,
                  source_id=profile['id'],revision=db['source_sha256'])
    return normalize(dict(lens_auto=True,lens_calibration=snapshot,lens_tca=bool(tca),lens_fill=True,lens_vignette=False)) | {
        'lens_profile':'Lensfun · '+profile['name'],'lens_k1':0,'lens_k2':0,'lens_red':0,'lens_blue':0,'vignette':0}


def radial(r,calibration):
    a,b,c=calibration['coefficients'];model=calibration['model']
    if model=='ptlens':
        d=1-a-b-c;return 1+c/d**2*r+b/d**3*r*r+a/d**4*r*r*r
    if model=='poly3':return 1+a/max(1-a,.2)**3*r*r
    return 1+a*r*r+b*r**4


def coordinate_scale(w,h,calibration):
    return math.hypot(w,h)/(2*math.hypot(calibration['aspect'],1))*calibration['image_crop']/calibration['crop']


@lru_cache(maxsize=64)
def fill_scale(w,h,encoded,tca):
    calibration=json.loads(encoded);scale=coordinate_scale(w,h,calibration)
    u=np.linspace(-1,1,65);cx=(w-1)/2;cy=(h-1)/2
    x=np.concatenate((u*cx,u*cx,np.full_like(u,-cx),np.full_like(u,cx)))
    y=np.concatenate((np.full_like(u,-cy),np.full_like(u,cy),u*cy,u*cy))
    def fits(zoom):
        xx=x/zoom;yy=y/zoom;r=np.hypot(xx,yy)/scale;factor=radial(r,calibration)
        for br,cr,vr in ([[0,0,1],calibration['tca'][:3],calibration['tca'][3:]] if tca else [[0,0,1]]):
            rd=r*factor;f=factor*(br*rd*rd+cr*rd+vr)
            if np.max(abs(xx*f))>cx or np.max(abs(yy*f))>cy:return False
        return True
    if fits(1):return 1.
    lo,hi=1.,3.
    if not fits(hi):raise ValueError('镜头变换无法安全填满，请关闭配置或调整裁剪')
    for _ in range(30):
        mid=(lo+hi)/2
        if fits(mid):hi=mid
        else:lo=mid
    return hi*1.00001


def correct(arr,p,sample):
    if not p.get('lens_auto'):return arr
    calibration=p['lens_calibration'];h,w=arr.shape[:2];cx=(w-1)/2;cy=(h-1)/2
    scale=coordinate_scale(w,h,calibration)
    zoom=fill_scale(w,h,json.dumps(calibration,sort_keys=True),p.get('lens_tca',True)) if p.get('lens_fill',True) else 1.
    xx=(np.arange(w,dtype=np.float32)[None,:]-cx)/zoom;out=np.empty_like(arr)
    for top in range(0,h,128):
        bottom=min(h,top+128);yy=(np.arange(top,bottom,dtype=np.float32)[:,None]-cy)/zoom
        r=np.sqrt(xx*xx+yy*yy)/scale;factor=radial(r,calibration)
        for channel in range(3):
            f=factor
            if p.get('lens_tca',True) and channel!=1:
                b,c,v=calibration['tca'][:3] if channel==0 else calibration['tca'][3:]
                rd=r*factor;f=factor*(b*rd*rd+c*rd+v)
            x=cx+xx*f;y=cy+yy*f
            out[top:bottom,:,channel]=sample(arr[...,channel],x,y)
            if p.get('lens_vignette'):
                radius2=((x-cx)**2+(y-cy)**2)/max(cx*cx+cy*cy,1)*(calibration['crop']/calibration['image_crop'])**2
                k1,k2,k3=calibration['vignetting'];out[top:bottom,:,channel]/=np.clip(1+k1*radius2+k2*radius2**2+k3*radius2**3,.25,4)
    return out


def estimate_tca(arr,current):
    """Conservative radial edge registration; return no-op for weak evidence."""
    from imaging import resize_linear
    from spatial import optical,_sample
    h,w=arr.shape[:2];scale=min(1,800/max(h,w));small=resize_linear(arr,(max(16,round(w*scale)),max(16,round(h*scale))))
    p=dict(current);small=optical(small,p);h,w=small.shape[:2];cx=(w-1)/2;cy=(h-1)/2
    xx=np.arange(w,dtype=np.float32)[None,:]-cx;yy=np.arange(h,dtype=np.float32)[:,None]-cy
    green=small[...,1];gy,gx=np.gradient(green);strength=np.hypot(gx,gy)
    radius=np.hypot(xx/max(cx,1),yy/max(cy,1))
    neutral=(small.max(axis=2)-small.min(axis=2))<np.maximum(small.max(axis=2),.01)*.5
    mask=(strength>np.quantile(strength,.85))&(radius>.3)&(radius<.85)&neutral
    if mask.sum()<150 or float(np.std(green))<.005:return dict(applied=False,message='有效边缘不足；保留现有色差设置')
    ref=np.stack((gx[mask],gy[mask]),axis=1).reshape(-1);ref/=max(np.linalg.norm(ref),1e-8)
    result={};improvements=[]
    for channel,key_name in ((0,'lens_red'),(2,'lens_blue')):
        def gradients(offset):
            a=_sample(small[...,channel],cx+xx*(1+offset/1000),cy+yy*(1+offset/1000));ay,ax=np.gradient(a)
            return ax,ay
        def loss(offset):
            ax,ay=gradients(offset);grad=np.stack((ax[mask],ay[mask]),axis=1).reshape(-1);grad/=max(np.linalg.norm(grad),1e-8)
            return float(np.mean((grad-ref)**2))
        base=loss(0);coarse=np.arange(-4,4.01,.25);best=min(coarse,key=loss)
        fine=np.arange(best-.25,best+.251,.05);best=float(min(fine,key=loss));after=loss(best)
        improvement=(base-after)/max(base,1e-12)
        ax0,ay0=gradients(0);ax1,ay1=gradients(best);consistent=0;tested=0
        for left,upper in ((True,True),(True,False),(False,True),(False,False)):
            area=mask&((xx<0) if left else (xx>=0))&((yy<0) if upper else (yy>=0))
            if area.sum()<25:continue
            tested+=1
            def regional(ax,ay):
                v=np.stack((ax[area],ay[area]),axis=1).ravel();v/=max(np.linalg.norm(v),1e-8);return v
            g=regional(gx,gy);before_region=np.mean((regional(ax0,ay0)-g)**2);after_region=np.mean((regional(ax1,ay1)-g)**2)
            consistent+=after_region<before_region*.98
        if improvement>.05 and .075<=abs(best)<3.75 and tested>=3 and consistent>=3:
            result[key_name]=max(-10,min(10,current.get(key_name,0)+best));improvements.append(improvement)
    return dict(applied=bool(result),settings=result,message=('已估计横向色差，请在高对比边缘检查并可撤销' if result else '未发现足够可靠的改善；保留现有色差设置'),
                max_edge_improvement=max(improvements,default=0))
