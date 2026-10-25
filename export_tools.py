"""Output recipes: float resizing/sharpening, watermark and safe metadata/names."""
from datetime import datetime
from fractions import Fraction
import io
import math
from pathlib import Path
import re
import string
import zlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import colour_management as colour
from processing import sharpen_float

DEFAULTS=dict(workers=1,long_edge=0,width=0,height=0,upscale=False,output_sharpen=0,watermark='',
              watermark_opacity=50,watermark_size=3,watermark_position='br',
              watermark_color='white',metadata='clear',name_template='{name}-Z30-{index:03d}')
PRESETS={'full':dict(name='原尺寸',format='JPEG',quality=95),
         'social':dict(name='分享 · 2048px',format='JPEG',quality=90,long_edge=2048,output_sharpen=25),
         'web':dict(name='网页 · 1600px',format='JPEG',quality=85,long_edge=1600,output_sharpen=20),
         'print':dict(name='打印 · TIFF16',format='TIFF16',quality=95,output_sharpen=35,metadata='basic')}

def normalize(data):
    if not isinstance(data,dict):raise ValueError('导出选项格式无效')
    p=DEFAULTS|{k:v for k,v in data.items() if k in DEFAULTS}
    if isinstance(p['workers'],bool) or p['workers'] not in (1,2):raise ValueError('导出并发只支持 1 或 2')
    for k,lo,hi in [('long_edge',0,30000),('width',0,30000),('height',0,30000),('output_sharpen',0,100),
                    ('watermark_opacity',0,100),('watermark_size',1,12)]:
        v=float(p[k])
        if not math.isfinite(v) or not lo<=v<=hi:raise ValueError('导出尺寸或强度无效')
        p[k]=int(v) if k in ('long_edge','width','height') else v
    if not isinstance(p['upscale'],bool):raise ValueError('放大选项无效')
    if p['width'] and p['height'] and p['width']*p['height']>100_000_000:raise ValueError('输出最多 1 亿像素')
    if p['metadata'] not in ('clear','basic'):raise ValueError('元数据策略无效')
    if p['watermark_position'] not in ('tl','tr','bl','br','center') or p['watermark_color'] not in ('white','black'):raise ValueError('水印选项无效')
    if not isinstance(p['watermark'],str) or len(p['watermark'])>200 or any(ord(c)<32 for c in p['watermark']):raise ValueError('水印需为 0–200 字单行文本')
    template=p['name_template']
    if not isinstance(template,str) or not 1<=len(template)<=160 or re.search(r'[\\/<>:"|?*\x00-\x1f]',template.replace('{index:02d}','').replace('{index:03d}','').replace('{index:04d}','')):raise ValueError('文件名模板含非法字符')
    try:
        for literal,field,fmt,conversion in string.Formatter().parse(template):
            if field is not None and (field not in ('name','index','date') or conversion or fmt not in ('','02d','03d','04d') or fmt and field!='index'):raise ValueError()
    except ValueError:raise ValueError('命名仅支持 {name}、{index}、{index:03d}、{date}') from None
    return p

def safe_name(name):
    name=re.sub(r'[\\/<>:"|?*\x00-\x1f]','_',str(name)).strip(' .')[:180].rstrip(' .') or 'photo'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',name):name='_'+name
    return name

def names(sources,options,extension,created):
    p=normalize(options);used=set();result=[]
    for i,source in enumerate(sources):
        stem=safe_name(p['name_template'].format(name=safe_name(Path(source['name']).stem),index=i+1,date=datetime.fromtimestamp(created).strftime('%Y%m%d')))
        candidate=stem+'.'+extension;n=1
        while candidate.casefold() in used:
            n+=1;candidate=f'{stem}-{n}.{extension}'
        used.add(candidate.casefold());result.append(candidate)
    return result

def dimensions(size,p):
    w,h=size;scale=1.
    if p['long_edge']:scale=p['long_edge']/max(size)
    if p['width'] or p['height']:scale=min(p['width']/w if p['width'] else float('inf'),p['height']/h if p['height'] else float('inf'))
    if not p['upscale']:scale=min(1,scale)
    result=max(1,round(w*scale)),max(1,round(h*scale))
    if result[0]*result[1]>100_000_000:raise ValueError('输出最多 1 亿像素')
    return result

def resized(pixels,space,p):
    h,w=pixels.shape[:2];size=dimensions((w,h),p)
    if size==(w,h):return pixels
    channels=[]
    for c in range(3):
        channel=colour.decode(pixels[...,c],space)
        channels.append(np.asarray(Image.fromarray(channel).resize(size,Image.Resampling.LANCZOS)))
    return colour.encode(np.stack(channels,axis=2),space)

def watermark_layer(size,p):
    w,h=size;margin=max(3,round(min(w,h)*.025));font_size=max(8,round(min(w,h)*p['watermark_size']/100))
    font_path=next((str(v) for v in (Path('C:/Windows/Fonts/msyh.ttc'),Path('C:/Windows/Fonts/arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')) if v.exists()),None)
    def font(size):return ImageFont.truetype(font_path,size) if font_path else ImageFont.load_default(size=size)
    f=font(font_size);bbox=f.getbbox(p['watermark'])
    while bbox[2]-bbox[0]>w-2*margin and font_size>8:
        font_size-=1;f=font(font_size);bbox=f.getbbox(p['watermark'])
    tw,th=min(w,bbox[2]-bbox[0]+4),min(h,bbox[3]-bbox[1]+4)
    mask=Image.new('L',(tw,th));ImageDraw.Draw(mask).text((2-bbox[0],2-bbox[1]),p['watermark'],fill=255,font=f)
    position=p['watermark_position'];x=margin if position.endswith('l') else w-tw-margin;y=margin if position.startswith('t') else h-th-margin
    if position=='center':x,y=(w-tw)//2,(h-th)//2
    x,y=max(0,min(w-tw,x)),max(0,min(h-th,y))
    return x,y,np.asarray(mask,dtype=np.float32)[...,None]*(p['watermark_opacity']/25500)

def output_tiles(pixels,space,p,rows=128,progress=None):
    h,w=pixels.shape[:2];layer=watermark_layer((w,h),p) if p['watermark'] else None
    for top in range(0,h,rows):
        bottom=min(h,top+rows);low=max(0,top-2);high=min(h,bottom+2)
        if p['output_sharpen']:
            tile=sharpen_float(pixels[low:high].copy(),p['output_sharpen'])[top-low:bottom-low]
        else:tile=pixels[top:bottom].copy()
        if layer:
            x,y,alpha=layer;a=max(top,y);b=min(bottom,y+len(alpha))
            if b>a:
                region=tile[a-top:b-top,x:x+alpha.shape[1]];weight=alpha[a-y:b-y]
                region*=1-weight;region+=weight*(1 if p['watermark_color']=='white' else 0)
        if progress:progress('encode',.8+.19*bottom/h)
        yield top,bottom,np.clip(tile,0,1,out=tile)

def finish(pixels,space,options):
    p=normalize(options);pixels=resized(pixels,space,p);out=np.empty_like(pixels)
    for top,bottom,tile in output_tiles(pixels,space,p):out[top:bottom]=tile
    return out


ASCII_TAGS=(271,272,306,315,33432,36867,36868,42036)
RATIONAL_TAGS=(33434,33437,37386)
INTEGER_TAGS=(34855,)

def metadata(path,policy):
    if policy=='clear':return {}
    with Image.open(path) as image:
        exif=image.getexif()
        try:exif=dict(exif)|dict(exif.get_ifd(34665))
        except (KeyError,TypeError,ValueError):exif=dict(exif)
    out={}
    for tag in ASCII_TAGS:
        value=exif.get(tag)
        if isinstance(value,str):out[tag]=value.replace('\x00','')[:200]
    for tag in RATIONAL_TAGS+INTEGER_TAGS:
        value=exif.get(tag)
        try:
            if value is not None and math.isfinite(float(value)) and 0<float(value)<1e9:out[tag]=value
        except (ValueError,TypeError):pass
    return out

def exif_bytes(values,size,space):
    if not values:return b''
    exif=Image.Exif();nested={}
    for k,v in values.items():
        if k>=33434 and k not in (33432,):nested[k]=v
        else:exif[k]=v
    exif[274]=1;exif[305]='Z30 Studio';nested.update({40961:1 if space=='srgb' else 65535,40962:size[0],40963:size[1]});exif[34665]=nested
    return exif.tobytes()

def tiff_tags(values):
    result=[]
    for tag,v in values.items():
        if tag in ASCII_TAGS:
            # TIFF ASCII is deliberately limited; PNG/JPEG EXIF uses Pillow.
            result.append((tag,'s',0,str(v).encode('ascii','replace').decode(),False))
        elif tag in RATIONAL_TAGS:
            f=Fraction(float(v)).limit_denominator(1_000_000);result.append((tag,5,1,(f.numerator,f.denominator),False))
        else:result.append((tag,'I',1,int(v),False))
    if result:result.append((274,'H',1,1,False))
    return result

def encode(pixels,space,fmt,quality,source,options,progress=None):
    p=normalize(options);pixels=resized(pixels,space,p);values=metadata(source,p['metadata']);out=io.BytesIO()
    h,w=pixels.shape[:2]
    if fmt=='TIFF16':
        import tifffile
        def strips():
            for top,bottom,tile in output_tiles(pixels,space,p,progress=progress):yield zlib.compress(np.rint(tile*65535).astype('<u2').tobytes())
        tifffile.imwrite(out,strips(),shape=(h,w,3),dtype=np.uint16,byteorder='<',photometric='rgb',iccprofile=colour.profile(space),metadata=None,
                         extratags=tiff_tags(values),software='Z30 Studio' if values else None,compression='deflate',maxworkers=1,rowsperstrip=128)
    else:
        quant=np.empty((h,w,3),np.uint8)
        for top,bottom,tile in output_tiles(pixels,space,p,progress=progress):quant[top:bottom]=np.rint(tile*255)
        image=Image.fromarray(quant);opts=dict(icc_profile=colour.profile(space))
        if fmt=='JPEG':opts.update(quality=quality,subsampling=0)
        exif=exif_bytes(values,image.size,space)
        if exif:opts['exif']=exif
        image.save(out,format=fmt,**opts)
    return out.getvalue()
