"""Float32 luminance tone mapping and hue-preserving perceptual colour controls."""
from functools import lru_cache
import math

import numpy as np

LUMA = np.array([.2126, .7152, .0722], np.float32)
RGB_LMS = np.array([[.4122214708, .5363325363, .0514459929],
                    [.2119034982, .6806995451, .1073969566],
                    [.0883024619, .2817188376, .6299787005]], np.float32)
LMS_LAB = np.array([[.2104542553, .7936177850, -.0040720468],
                    [1.9779984951, -2.4285922050, .4505937099],
                    [.0259040371, .7827717662, -.8086757660]], np.float32)
LAB_LMS = np.linalg.inv(LMS_LAB).astype(np.float32)
LMS_RGB = np.linalg.inv(RGB_LMS).astype(np.float32)


def rgb_to_oklab(rgb):
    lms = rgb @ RGB_LMS.T
    if lms.min() >= 0:
        np.power(lms, np.float32(1 / 3), out=lms)
    else:
        np.cbrt(lms, out=lms)
    return lms @ LMS_LAB.T


def oklab_to_rgb(lab):
    lms = lab @ LAB_LMS.T
    lms *= lms * lms
    return lms @ LMS_RGB.T


def channel_bounds(rgb):
    return (np.minimum(np.minimum(rgb[..., 0], rgb[..., 1]), rgb[..., 2]),
            np.maximum(np.maximum(rgb[..., 0], rgb[..., 1]), rgb[..., 2]))


def gamut_map(lab):
    """Reduce only out-of-gamut chroma at fixed OKLab lightness and hue."""
    lab[..., 0] = np.clip(lab[..., 0], 0, 1)
    rgb = oklab_to_rgb(lab)
    low, high = channel_bounds(rgb)
    mask = (low < -1e-6) | (high > 1 + 1e-6)
    if mask.any():
        selected = lab[mask].copy()
        lo = np.zeros(selected.shape[0], np.float32)
        hi = np.ones_like(lo)
        original = selected[:, 1:].copy()
        for _ in range(10):
            mid = (lo + hi) * .5
            selected[:, 1:] = original * mid[:, None]
            test = oklab_to_rgb(selected)
            lower, upper = channel_bounds(test)
            good = (lower >= 0) & (upper <= 1)
            lo = np.where(good, mid, lo); hi = np.where(good, hi, mid)
        selected[:, 1:] = original * lo[:, None]
        rgb[mask] = oklab_to_rgb(selected)
    return np.clip(rgb, 0, 1)


def tone_luminance(y, p):
    """Black-anchored, monotonic tone curve with a C1 rational shoulder."""
    y = np.maximum(y, 0)
    y = y * np.exp2(np.float32(p['shadows'] / 100 * 1.2) * np.exp(-y / .18))
    if p['contrast']:
        y = .18 * np.power(y / .18, np.float32(2 ** (p['contrast'] / 200)))
    if p['highlights']:
        y = y * np.exp2(np.float32(p['highlights'] / 100) * (y / (y + .3)))
    knee = np.float32(.65)
    above = np.maximum(y - knee, 0)
    # A rational tail retains separation in bright values where an exponential
    # asymptote can round prematurely to 1.0 even in a 16-bit export.
    return np.where(y <= knee, y, knee + (1 - knee) * above / (above + (1 - knee)))


@lru_cache(maxsize=32)
def _tone_table(shadows, contrast, highlights, ceiling):
    root = np.linspace(0, math.sqrt(ceiling), 32769, dtype=np.float32)
    table = tone_luminance(root * root, dict(shadows=shadows, contrast=contrast, highlights=highlights))
    table.setflags(write=False)
    return table


def fast_tone(y, p):
    """Quadratic-spaced interpolation; checked against the analytic float curve."""
    ceiling = 2 ** math.ceil(math.log2(max(1, float(y.max()))))
    table = _tone_table(p['shadows'], p['contrast'], p['highlights'], ceiling)
    position = np.sqrt(np.maximum(y, 0) / np.float32(ceiling)) * 32768
    index = np.minimum(position.astype(np.int32), 32767)
    fraction = position - index.astype(np.float32)
    return table[index] + (table[index + 1] - table[index]) * fraction


def display_chunk(arr, p):
    gains = np.array([2 ** (p['temperature'] / 180), 2 ** (-p['tint'] / 220),
                      2 ** (-p['temperature'] / 180)], np.float32)
    gains /= gains @ LUMA  # WB offsets do not silently change neutral exposure.
    x = np.multiply(arr, gains * np.float32(2 ** p['exposure']), dtype=np.float32)
    y = x @ LUMA
    mapped = fast_tone(y, p)
    ratio = np.divide(mapped, y, out=np.zeros_like(y), where=y > 1e-12)
    x *= ratio[..., None]
    needs_gamut = bool(x.min() < 0 or x.max() > 1)
    if p['saturation'] or p['vibrance'] or needs_gamut:
        lab = rgb_to_oklab(x)
        if p['saturation'] or p['vibrance']:
            chroma = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
            hue = np.arctan2(lab[..., 2], lab[..., 1])
            # A soft hue band moderates positive boosts in warm low/mid chroma.
            # This is not a face/skin detector and never changes hue directly.
            warm = np.exp(-((hue - .8) / .55) ** 2) * np.exp(-(chroma / .2) ** 2)
            sat = p['saturation'] / 100
            factor = (1 + sat * (1 - .55 * warm) if sat > 0 else 1 + sat)
            factor = factor * (1 + p['vibrance'] / 100 * np.exp(-chroma / .12) * (1 - .6 * warm))
            lab[..., 1:] *= np.asarray(factor, np.float32)[..., None] if np.ndim(factor) else factor
        x = gamut_map(lab)
    np.maximum(x, 0, out=x)
    # Avoid np.where's eager allocation of both three-channel branches.
    encoded = np.power(x, np.float32(1 / 2.4))
    encoded *= 1.055; encoded -= .055
    low = x <= .0031308
    encoded[low] = x[low] * 12.92
    return np.clip(encoded, 0, 1, out=encoded)


def sharpen_float(rgb, amount):
    """Float luminance unsharp mask, with no 8-bit detour for TIFF16."""
    y = rgb @ LUMA
    pad = np.pad(y, ((0, 0), (2, 2)), mode='edge')
    horizontal = (pad[:, :-4] + 4 * pad[:, 1:-3] + 6 * pad[:, 2:-2] +
                  4 * pad[:, 3:-1] + pad[:, 4:]) / 16
    pad = np.pad(horizontal, ((2, 2), (0, 0)), mode='edge')
    blur = (pad[:-4] + 4 * pad[1:-3] + 6 * pad[2:-2] + 4 * pad[3:-1] + pad[4:]) / 16
    detail = y - blur
    detail *= np.minimum(abs(detail) / (3 / 255), 1)
    target = np.maximum(y + detail * np.float32(amount / 75), 0)
    gain = np.divide(target, y, out=np.ones_like(y), where=y > 1e-10)
    gain = np.minimum(gain, 1 / np.maximum(channel_bounds(rgb)[1], 1e-10))
    rgb *= gain[..., None]
    return rgb


def render_array(arr, p, bits=8, chunk_rows=128):
    if bits not in (8, 16):
        raise ValueError('Output depth must be 8 or 16')
    if arr.dtype != np.float32:
        raise ValueError('调色输入必须是 float32 线性 RGB 数据')
    dtype, scale = (np.uint8, 255) if bits == 8 else (np.uint16, 65535)
    output = np.empty(arr.shape, dtype=dtype)
    halo = 2 if p['sharpen'] else 0
    for start in range(0, arr.shape[0], chunk_rows):
        end = min(start + chunk_rows, arr.shape[0])
        low, high = max(0, start - halo), min(arr.shape[0], end + halo)
        if not np.isfinite(arr[low:high]).all():
            raise ValueError('调色输入包含非有限数据')
        rgb = display_chunk(arr[low:high], p)
        if halo:
            rgb = sharpen_float(rgb, p['sharpen'])
        rgb = rgb[start - low:end - low]
        rgb *= scale
        np.rint(rgb, out=rgb)
        output[start:end] = rgb
    return output
