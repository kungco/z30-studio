"""Independent NumPy reference for local float skin-tone controls.

Uses actual native float input and source-bound public image masks; no cloud,
user portraits or inference-accuracy claims. RGB tolerance is fixed at 2e-6.
"""
import argparse,json,tempfile
from pathlib import Path
import numpy as np
from PIL import Image,ImageOps
from validate_native_skin import measured,resized,digest,IMAGE_HASH

F=np.float32
def luma(a):return a[...,0]*F(.2126)+a[...,1]*F(.7152)+a[...,2]*F(.0722)
def ramp(lo,hi,v):
    t=np.clip((v-F(lo))/F(hi-lo),0,1);return t*t*(F(3)-F(2)*t)
def geometry(face,width,height,ow,oh):
    x=(np.arange(width,dtype=np.float64)+.5)*ow/width
    y=(np.arange(height,dtype=np.float64)+.5)*oh/height
    x,y=np.meshgrid(x.astype(np.float32),y.astype(np.float32))
    left,top,w,h=map(F,face['rect']);fw,fh=w*F(ow),h*F(oh)
    cx=(left+w*F(.5))*F(ow);cy=top*F(oh)+fh*F(.55)
    ellipse=((x-cx)/max(fw*F(.46),F(1)))**2+((y-cy)/max(fh*F(.45),F(1)))**2
    weight=ramp(0,.35,F(1)-ellipse)
    p=np.array([[v['x']*ow,v['y']*oh] for v in face['landmarks']],dtype=np.float32)
    distance=max(F(np.sqrt(np.sum((p[0]-p[1])**2,dtype=np.float32))),F(1))
    def hole(cx,cy,rx,ry):return ramp(1,2,((x-cx)/max(rx,F(1)))**2+((y-cy)/max(ry,F(1)))**2)
    for eye in p[:2]:weight*=hole(eye[0],eye[1]-distance*F(.05),distance*F(.24),distance*F(.18))
    mouth=F(np.sqrt(np.sum((p[3]-p[4])**2,dtype=np.float32)))
    weight*=hole((p[3,0]+p[4,0])*F(.5),(p[3,1]+p[4,1])*F(.5),mouth*F(.75),distance*F(.18))
    return weight
def box(a,rx,ry):
    # Independent summed-area implementation; no sliding-window Rust reuse.
    padded=np.pad(a,((ry,ry),(rx,rx),(0,0)),mode='edge').astype(np.float64)
    total=np.pad(np.cumsum(np.cumsum(padded,axis=0),axis=1),((1,0),(1,0),(0,0)))
    sy,sx=2*ry+1,2*rx+1
    return ((total[sy:,sx:]-total[:-sy,sx:]-total[sy:,:-sx]+total[:-sy,:-sx])/(sx*sy)).astype(np.float32)
def reference(src,weights,face,uniformity,lighten):
    h,w=src.shape[:2];value=luma(src);dark=ramp(.02,.12,value)
    gate=resized(weights[...,None].astype(np.float32)/F(255),w,h)[...,0]*geometry(face,w,h,w,h)
    out=src.copy()
    if uniformity:
        model=resized(src);y=luma(model);alpha=weights.astype(np.float32)/F(255)*geometry(face,256,256,w,h)
        alpha=np.where(y>F(.02),alpha,F(0));safe=np.maximum(y,F(.00001))
        field=np.concatenate((model/safe[...,None]*alpha[...,None],alpha[...,None],(y*alpha)[...,None]),axis=-1)
        rx=int(np.clip(np.floor(F(face['rect'][2])*F(256)*F(.035)+F(.5)),1,12))
        ry=int(np.clip(np.floor(F(face['rect'][3])*F(256)*F(.035)+F(.5)),1,12))
        mean=box(field,rx,ry);a=np.maximum(field[...,3],F(.000001));b=np.maximum(mean[...,3],F(.000001))
        edge=F(1)-ramp(.04,.18,np.abs(field[...,4]/a-mean[...,4]/b))
        delta=np.clip(mean[...,:3]/b[...,None]-field[...,:3]/a[...,None],F(-.08),F(.08))*edge[...,None]
        delta=np.where(((field[...,3]>=F(.01))&(mean[...,3]>=F(.01)))[...,None],delta,F(0))
        delta=resized(delta,w,h)
        proposed=src/np.maximum(value,F(.00001))[...,None]+delta*F(uniformity/100)*F(.6)*gate[...,None]*dark[...,None]
        offset=proposed/np.maximum(luma(proposed),F(.00001))[...,None]*value[...,None]-src
        limits=np.ones_like(offset)
        np.divide(F(1)-src,offset,out=limits,where=offset>0)
        np.divide(-src,offset,out=limits,where=offset<0)
        step=np.clip(limits.min(axis=-1),0,1)
        out=np.clip(src+offset*step[...,None],0,1)
    if lighten:
        now=luma(out);target=now+F(lighten/100)*F(.045)*(F(1)-now)*gate*dark*(F(1)-ramp(.75,.98,now))
        ratio=np.minimum(target/np.maximum(now,F(.00001)),F(1)/np.maximum(out.max(axis=-1),F(.00001)))
        out=np.clip(out*ratio[...,None],0,1)
    return np.where(((gate>0)&(dark>0))[...,None],out,src)
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('probe','runtime','model','image','out'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();assert not args.out.exists() and digest(args.image)==IMAGE_HASH
    root=Path(__file__).resolve().parents[1];scratch=Path(tempfile.mkdtemp(prefix='tone-reference-',dir=root/'.development'))
    original=Image.open(args.image).convert('RGB');records=[];failures=[]
    for name,image in [('original',original),('wide',ImageOps.pad(original,(800,512))),('tall',ImageOps.pad(original,(512,800))),('crop',original.crop((150,30,300,210)))]:
        photo=scratch/(name+'.png');image.save(photo);output=scratch/name
        native=measured([str(args.probe.resolve()),str(args.runtime.resolve()),str(args.model.resolve()),str(photo),str(output)])
        assert native['faces'];w,h=image.size
        source=np.fromfile(output/'semantic.f32',dtype='<f4').reshape(h,w,3);weights=np.fromfile(output/'weights.u8',dtype=np.uint8).reshape(256,256)
        for mode,u,b in [('uniform',100,0),('lighten',0,100)]:
            actual=np.fromfile(output/(mode+'.f32'),dtype='<f4').reshape(h,w,3)
            expected=reference(source,weights,native['faces'][0],u,b);delta=np.abs(actual-expected)
            gate=resized(weights[...,None].astype(np.float32)/F(255),w,h)[...,0]*geometry(native['faces'][0],w,h,w,h)
            excluded=gate==0
            row=dict(case=name,mode=mode,max_error=float(delta.max()),mean_error=float(delta.mean()),
                excluded_pixels_exact=bool(np.array_equal(actual[excluded],source[excluded])),
                finite_in_gamut=bool(np.isfinite(actual).all() and actual.min()>=0 and actual.max()<=1),
                max_luminance_change=float(np.abs(luma(actual)-luma(source)).max()))
            if row['max_error']>2e-6 or not row['excluded_pixels_exact'] or not row['finite_in_gamut'] or (mode=='uniform' and row['max_luminance_change']>1e-6):failures.append(name+':'+mode)
            Image.fromarray(np.round(np.clip(actual,0,1)*255).astype(np.uint8)).save(output/(mode+'.png'))
            records.append(row)
    report=dict(stage='5D tone numeric reference',threshold_max_rgb_error=2e-6,cases=records,failures=failures,passed=not failures,
                scratch=str(scratch),public_image_unchanged=digest(args.image)==IMAGE_HASH,user_z30_portrait=False,cloud_requests=0)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(dict(passed=not failures,cases=len(records),failures=failures)))
    if failures:raise SystemExit(1)
if __name__=='__main__':main()
