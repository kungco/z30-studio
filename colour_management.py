"""Float matrix colour spaces and preview-only ICC display / print proofing.

Chromaticities and transfer functions follow W3C CSS Color 4. Generated ICCs
contain our matrix/TRC data, not redistributed vendor profile binaries.
"""
from functools import lru_cache
import hashlib
import io
import re
import struct
from pathlib import Path
import numpy as np
from PIL import ImageCms
from imaging import rgb_matrix, SRGB_TO_XYZ, SRGB_XY, ADOBE_XY, srgb_to_linear

SPACES = {'srgb':'sRGB', 'adobe-rgb':'Adobe RGB (1998)', 'prophoto':'ProPhoto RGB'}
DEFAULTS = dict(output_space='srgb',display_profile='',proof_profile='',proof_enabled=False,
                proof_intent='relative',proof_paper=False,proof_gamut=False)
PROPHOTO_XY=(.3457,.3585,.7347,.2653,.1596,.8404,.0366,.0001)
BRADFORD=np.array([[.8951,.2664,-.1614],[-.7502,1.7135,.0367],[.0389,-.0685,1.0296]])
D65=SRGB_TO_XYZ@np.ones(3)
D50=np.array([.9642,1,.8249])  # ICC PCS illuminant, per ICC specification.

def adaptation(source,target):
    return np.linalg.solve(BRADFORD,np.diag((BRADFORD@target)/(BRADFORD@source))@BRADFORD)

MATRICES={'srgb':SRGB_TO_XYZ,'adobe-rgb':rgb_matrix(ADOBE_XY),'prophoto':rgb_matrix(PROPHOTO_XY)}
TO_WORK={k:np.linalg.solve(SRGB_TO_XYZ,v).astype(np.float32) for k,v in MATRICES.items()}
FROM_WORK={k:np.linalg.inv(v).astype(np.float32) for k,v in TO_WORK.items()}

def normalize(p):
    out=DEFAULTS.copy();out.update({k:p[k] for k in out if k in p})
    if out['output_space'] not in SPACES:raise ValueError('无效输出色彩空间')
    for k in ('display_profile','proof_profile'):
        if not isinstance(out[k],str) or out[k] and not re.fullmatch('[a-f0-9]{64}',out[k]):raise ValueError('ICC 配置编号无效')
    for k in ('proof_enabled','proof_paper','proof_gamut'):
        if not isinstance(out[k],bool):raise ValueError('软打样选项无效')
    if out['proof_intent'] not in ('relative','perceptual','absolute'):raise ValueError('软打样意图无效')
    if out['proof_enabled'] and not out['proof_profile']:raise ValueError('请先选择软打样 ICC')
    return out

def to_working(rgb,space):
    return rgb if space=='srgb' else rgb@TO_WORK[space].T

def from_working(rgb,space):
    return rgb if space=='srgb' else rgb@FROM_WORK[space].T

def decode(rgb,space):
    if space=='srgb':return srgb_to_linear(rgb)
    if space=='adobe-rgb':return np.maximum(rgb,0)**np.float32(563/256)
    return np.where(rgb<=1/32,rgb/16,np.maximum(rgb,0)**np.float32(1.8))

def encode(rgb,space):
    rgb=np.maximum(rgb,0)
    if space=='srgb':
        out=np.power(rgb,np.float32(1/2.4));out*=1.055;out-=.055
        low=rgb<=.0031308;out[low]=rgb[low]*12.92
    elif space=='adobe-rgb':out=rgb**np.float32(256/563)
    else:out=np.where(rgb<=1/512,rgb*16,rgb**np.float32(1/1.8))
    return np.clip(out,0,1,out=out)

def _xyz(v):return b'XYZ '+bytes(4)+struct.pack('>3i',*(round(float(x)*65536) for x in v))
def _mluc(s):
    value=s.encode('utf-16be')
    return b'mluc'+bytes(4)+struct.pack('>II',1,12)+b'enUS'+struct.pack('>II',len(value),28)+value

@lru_cache(maxsize=3)
def profile(space):
    if space=='srgb':return ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    matrix=adaptation(D65,D50)@MATRICES[space]
    chad=adaptation(D65,D50) if space=='adobe-rgb' else np.eye(3)
    if space=='adobe-rgb':trc=b'curv'+bytes(4)+struct.pack('>IH',1,563)
    else:
        trc=b'para'+bytes(4)+struct.pack('>HH5i',3,0,*(round(v*65536) for v in (1.8,1,0,1/16,1/32)))
    tags={b'wtpt':_xyz(D50),b'desc':_mluc('Z30 Studio '+SPACES[space]),
          b'cprt':_mluc('Generated matrix/TRC profile by Z30 Studio'),
          b'chad':b'sf32'+bytes(4)+struct.pack('>9i',*(round(v*65536) for v in chad.flat))}
    for i,c in enumerate('rgb'):tags[(c+'XYZ').encode()]=_xyz(matrix[:,i]);tags[(c+'TRC').encode()]=trc
    header=bytearray(128);header[8:12]=bytes([4,0x30,0,0]);header[12:24]=b'mntrRGB XYZ '
    header[24:36]=struct.pack('>6H',2026,1,1,0,0,0);header[36:40]=b'acsp';header[68:80]=_xyz(D50)[8:]
    table=bytearray(struct.pack('>I',len(tags)));payload=bytearray();offset=132+12*len(tags)
    for name,data in tags.items():
        table+=name+struct.pack('>II',offset+len(payload),len(data));payload+=data+bytes(-len(data)%4)
    result=header+table+payload;struct.pack_into('>I',result,0,len(result));return bytes(result)

def matrix_input(encoded,blob):
    """Decode matrix/TRC RGB ICCs without clipping at an 8-bit sRGB conversion.

    LUT ICCs explicitly return None and use the existing LCMS fallback instead.
    """
    if not 132<=len(blob)<=4*1024**2:return None
    count=struct.unpack_from('>I',blob,128)[0]
    if count>128 or 132+count*12>len(blob):return None
    tags={}
    for i in range(count):
        name,offset,length=struct.unpack_from('>4sII',blob,132+i*12)
        if offset+length>len(blob):return None
        tags[name]=blob[offset:offset+length]
    if any(k in tags for k in (b'A2B0',b'A2B1',b'D2B0')):return None
    def curve(x,b):
        if b[:4]==b'curv':
            n=struct.unpack_from('>I',b,8)[0]
            if n==0:return x
            if n==1:return x**np.float32(struct.unpack_from('>H',b,12)[0]/256)
            if not 2<=n<=65536 or len(b)<12+2*n:raise ValueError()
            samples=np.frombuffer(b[12:12+2*n],'>u2').astype(np.float32)/65535
            if np.any(np.diff(samples)<0):raise ValueError()
            return np.interp(x,np.linspace(0,1,n),samples).astype(np.float32)
        if b[:4]!=b'para':raise ValueError()
        kind=struct.unpack_from('>H',b,8)[0];n={0:1,1:3,2:4,3:5,4:7}.get(kind)
        if not n or len(b)<12+4*n:raise ValueError()
        v=np.array(struct.unpack_from('>'+str(n)+'i',b,12),np.float32)/65536;g=v[0]
        if g<=0:raise ValueError()
        if kind==0:return x**g
        a,b=v[1:3]
        if a<=0:raise ValueError()
        power=np.maximum(a*x+b,0)**g
        if kind in (1,2):return np.where(x>=-b/a,power,0)+(v[3] if kind==2 else 0)
        c,d=v[3:5]
        return np.where(x>=d,power+(v[5] if kind==4 else 0),c*x+(v[6] if kind==4 else 0))
    try:
        matrix=np.stack([np.array(struct.unpack_from('>3i',tags[(c+'XYZ').encode()],8))/65536 for c in 'rgb'],axis=1)
        transform=np.linalg.solve(SRGB_TO_XYZ,adaptation(D50,D65)@matrix).astype(np.float32)
        out=np.stack([curve(encoded[...,i],tags[(c+'TRC').encode()]) for i,c in enumerate('rgb')],axis=-1)
        if not np.isfinite(transform).all() or abs(np.linalg.det(transform))<1e-8:return None
        result=out@transform.T
        return result if np.isfinite(result).all() else None
    except (KeyError,ValueError,struct.error):return None

def as_srgb(image):
    blob=image.info.get('icc_profile')
    if not blob or blob==profile('srgb'):return image
    return ImageCms.profileToProfile(image,ImageCms.ImageCmsProfile(io.BytesIO(blob)),
                                    ImageCms.ImageCmsProfile(io.BytesIO(profile('srgb'))),outputMode='RGB')

class Profiles:
    def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
    def read(self,key):
        if not re.fullmatch('[a-f0-9]{64}',key or ''):raise ValueError('ICC 编号无效')
        try:return (self.root/(key+'.icc')).read_bytes()
        except OSError:raise ValueError('本机缺少此 ICC，请重新导入或关闭软打样') from None
    def describe(self,blob):
        if not 128<=len(blob)<=4*1024**2 or blob[36:40]!=b'acsp' or struct.unpack_from('>I',blob)[0]!=len(blob):raise ValueError('ICC 文件无效或超过 4 MiB')
        try:
            p=ImageCms.ImageCmsProfile(io.BytesIO(blob));space=p.profile.xcolor_space.strip();kind=p.profile.device_class.strip()
            if space not in ('RGB','CMYK') or kind not in ('mntr','prtr','scnr'):raise ValueError('只支持 RGB 显示器/输入和 RGB/CMYK 打印配置')
            return dict(id=hashlib.sha256(blob).hexdigest(),name=ImageCms.getProfileDescription(p).strip()[:200],space=space,kind=kind)
        except (OSError,ImageCms.PyCMSError):raise ValueError('ICC 无法读取') from None
    def add(self,blob):
        info=self.describe(blob);path=self.root/(info['id']+'.icc')
        if not path.exists():
            if len(list(self.root.glob('*.icc')))>=32:raise ValueError('最多保留 32 个本机 ICC')
            part=path.with_suffix('.part');part.write_bytes(blob);part.replace(path)
        return info
    def list(self):
        out=[]
        for path in self.root.glob('*.icc'):
            try:out.append(self.describe(path.read_bytes()))
            except ValueError:pass
        return out
    def preview(self,image,p):
        display=p.get('display_profile');proof=p.get('proof_enabled')
        if not display and not proof:return image
        src=ImageCms.ImageCmsProfile(io.BytesIO(image.info.get('icc_profile',profile('srgb'))))
        blob=self.read(display) if display else profile('srgb');target=ImageCms.ImageCmsProfile(io.BytesIO(blob))
        if target.profile.xcolor_space.strip()!='RGB':raise ValueError('显示器 ICC 必须为 RGB')
        try:
            if proof:
                proof_icc=ImageCms.ImageCmsProfile(io.BytesIO(self.read(p['proof_profile'])))
                intents={'relative':1,'perceptual':0,'absolute':3}
                flags=ImageCms.Flags.SOFTPROOFING|ImageCms.Flags.BLACKPOINTCOMPENSATION
                if p.get('proof_gamut'):flags|=ImageCms.Flags.GAMUTCHECK
                transform=ImageCms.buildProofTransformFromOpenProfiles(src,target,proof_icc,'RGB','RGB',
                    renderingIntent=intents[p['proof_intent']],proofRenderingIntent=3 if p.get('proof_paper') else 1,flags=flags)
                result=ImageCms.applyTransform(image,transform)
            else:result=ImageCms.profileToProfile(image,src,target,outputMode='RGB')
        except ImageCms.PyCMSError as e:raise ValueError('ICC 无法用于当前显示/软打样转换') from e
        result.info['icc_profile']=blob;return result
