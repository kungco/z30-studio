"""Photo catalog metadata and explicit deep-copied manual synchronization groups."""
import copy

GROUPS = {
    'light':('exposure','contrast','highlights','shadows'),
    'color':('temperature','tint','saturation','vibrance','wb','wb_mode','kelvin','wb_gains','wb_calibration','curves','hsl','grading'),
    'detail':('sharpen','clarity','texture','dehaze','denoise_luma','denoise_chroma'),
    'geometry':('rotation','crop','crop_rect','straighten'),
    'local':('masks','repairs','skin_smooth','skin_mask'),
    'optics':('lens_k1','lens_k2','lens_red','lens_blue','vignette','lens_profile','lens_auto','lens_tca'),
}


def catalog(value=None):
    if value is None:value={}
    if not isinstance(value,dict) or set(value)-{'rating','flag','keywords'}:raise ValueError('选片信息无效')
    rating=value.get('rating',0);flag=value.get('flag','none');keywords=value.get('keywords',[])
    if isinstance(rating,bool) or not isinstance(rating,int) or not 0<=rating<=5 or flag not in ('none','pick','reject'):
        raise ValueError('星级或旗标无效')
    if not isinstance(keywords,list) or len(keywords)>32 or any(not isinstance(k,str) or len(k)>48 for k in keywords):
        raise ValueError('关键词最多 32 个，每个不超过 48 字')
    return dict(rating=rating,flag=flag,keywords=list(dict.fromkeys(k.strip() for k in keywords if k.strip())))


def transfer(base,source,groups):
    if not isinstance(groups,list) or not groups or len(set(groups))!=len(groups) or any(g not in GROUPS for g in groups):
        raise ValueError('请选择有效的同步参数组')
    result=copy.deepcopy(base)
    for group in groups:
        for key in GROUPS[group]:
            if key in source:result[key]=copy.deepcopy(source[key])
    return result
