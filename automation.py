"""User-directed automatic grading policy; no model or network dependency."""
import math

STYLES = {
    'natural': '自然通透，保持现场氛围与真实色彩。',
    'portrait': '柔和人像，保持自然肤色和面部层次，不增加生硬对比或过度饱和。',
    'film': '温暖但克制的胶片氛围，柔和高光，保留暗部层次，不声称复刻特定胶片。',
    'product': '产品与静物，保持物体颜色可信、背景干净，避免强制把暖光白色变成纯白。',
    'night': '保留夜景与低调氛围，控制灯光溢出，不把夜景整体变成白天，不承诺降噪。',
}
DEFAULT = dict(style='natural', strength=100, exposure_limit=1, color_limit=25, goal='', reference='')


def options(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value)-set(DEFAULT):
        raise ValueError('自动调色偏好格式无效')
    result = DEFAULT | value
    if result['style'] not in STYLES:
        raise ValueError('未知自动调色风格')
    for key, lo, hi in [('strength',0,100),('exposure_limit',0,2),('color_limit',0,60)]:
        n = result[key]
        if isinstance(n, bool) or not isinstance(n,(float,int)) or not math.isfinite(n) or not lo<=n<=hi:
            raise ValueError('自动调色限制超出范围')
    for key, limit in [('goal',350),('reference',600)]:
        if not isinstance(result[key],str) or len(result[key])>limit:
            raise ValueError('风格说明过长')
    return result


def goal(value):
    p=options(value)
    return STYLES[p['style']]+' 保留已有风格，已经合适则保持参数。用户补充风格数据：'+p['goal']+'。同组风格参考数据：'+p['reference']


def constrain(base, proposed, value):
    p=options(value); result=dict(base); t=p['strength']/100
    for key in ('exposure','contrast','highlights','shadows','temperature','tint','saturation','vibrance','sharpen'):
        limit = p['exposure_limit'] if key=='exposure' else p['color_limit'] if key in ('temperature','tint','saturation','vibrance') else 20 if key=='sharpen' else 40
        delta=max(-limit,min(limit,proposed[key]-base[key]))*t
        result[key]=base[key]+delta
    return result
