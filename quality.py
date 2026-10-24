"""Explainable preview checks, not a photographic aesthetic/face/noise model."""
import numpy as np
from PIL import Image

from imaging import resize_linear, srgb_to_linear
from processing import rgb_to_oklab, LUMA
from codex_assist import RANGES


def pixels(image):
    scale = min(1, 384 / max(image.size))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return np.asarray(image.resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 255


def measure(image):
    rgb = pixels(image)
    y = rgb @ LUMA
    lab = rgb_to_oklab(srgb_to_linear(rgb))
    chroma = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    q05, q95 = np.quantile(y, [.05, .95])
    lap = (-4 * y[1:-1, 1:-1] + y[:-2, 1:-1] + y[2:, 1:-1] + y[1:-1, :-2] + y[1:-1, 2:])
    result = dict(channel_clip=float(np.mean(rgb.max(axis=2) >= 254.5 / 255)),
                  white_pixels=float(np.mean(rgb.min(axis=2) >= 254.5 / 255)),
                  black_pixels=float(np.mean(rgb.max(axis=2) <= .5 / 255)),
                  highlights=float(np.mean(y >= .97)), tone_span=float(q95 - q05),
                  chroma95=float(np.quantile(chroma, .95)),
                  detail_proxy=float(np.mean(lap ** 2)) if lap.size else 0,
                  noise_texture_proxy=float(np.median(abs(lap)) / 6) if lap.size else 0)
    warnings = []
    if result['channel_clip'] > .02:
        warnings.append('存在达到输出上限的颜色通道')
    if result['tone_span'] < .06:
        warnings.append('亮度范围较窄；可能是场景特征，不能仅凭此判断画质')
    result['warnings'] = warnings
    result['message'] = '；'.join(warnings) or '未发现明显的输出通道溢出或层次压缩'
    return result


def degradation(before, after, reference, candidate):
    reasons = []
    if after['channel_clip'] - before['channel_clip'] > max(.02, before['channel_clip'] * .75):
        reasons.append('输出通道到顶比例明显增加')
    if after['white_pixels'] - before['white_pixels'] > .03 or after['highlights'] - before['highlights'] > .30:
        reasons.append('大面积亮部趋于过白')
    if before['tone_span'] > .08 and after['tone_span'] < before['tone_span'] * .65:
        reasons.append('亮度层次范围显著缩小')
    if after['chroma95'] > before['chroma95'] * 1.5 + .035:
        reasons.append('色度提升过强')
    a, b = (rgb_to_oklab(srgb_to_linear(pixels(im))) for im in (reference, candidate))
    if a.shape == b.shape:
        hue_a, hue_b = np.arctan2(a[..., 2], a[..., 1]), np.arctan2(b[..., 2], b[..., 1])
        ca = np.hypot(a[..., 1], a[..., 2]); cb = np.hypot(b[..., 1], b[..., 2])
        mask = (hue_a > .25) & (hue_a < 1.3) & (ca > .03) & (ca < .2) & (cb > .02) & (a[..., 0] > .25)
        if mask.sum() > 20:
            error = abs(np.arctan2(np.sin(hue_b - hue_a), np.cos(hue_b - hue_a)))
            if float(np.quantile(error[mask], .9)) > np.deg2rad(12):
                reasons.append('暖色区色相偏移较大（不是人脸检测）')
    return reasons


def blend(base, proposed, strength):
    # Composition and categorical white balance remain under the user's control.
    return {**base, **{key: base[key] + (proposed[key] - base[key]) * strength for key in RANGES}}


def guard(linear, base, proposed, render):
    scale = min(1, 512 / max(linear.shape[:2]))
    if scale < 1:
        linear = resize_linear(linear, (max(1, round(linear.shape[1] * scale)), max(1, round(linear.shape[0] * scale))))
    reference = render(linear, base)
    before = measure(reference)
    initial_reasons = []
    for strength in (1.0, .75, .5, .25, 0.0):
        p = blend(base, proposed, strength)
        candidate = render(linear, p)
        after = measure(candidate)
        reasons = degradation(before, after, reference, candidate)
        if strength == 1:
            initial_reasons = reasons
        if not reasons or strength == 0:
            return p, dict(strength=strength, reasons=initial_reasons, before=before, after=after,
                           message=('画质保护将建议降至 ' + str(round(strength * 100)) + '%：' + '；'.join(initial_reasons))
                           if strength < 1 else '画质检查通过；仍需按实际画面判断效果')
