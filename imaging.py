"""Coordinate and input colour contracts shared by preview and full decoding."""
import io
import math

import numpy as np
from PIL import Image, ImageCms, ImageOps

SRGB = ImageCms.createProfile('sRGB')
SRGB_XY = (.3127, .3290, .64, .33, .30, .60, .15, .06)
ADOBE_XY = (.3127, .3290, .64, .33, .21, .71, .15, .06)


def srgb_to_linear(x):
    return np.where(x <= .04045, x / 12.92, ((np.maximum(x, 0) + .055) / 1.055) ** 2.4)


def raw_crop(s):
    """LibRaw inset coordinates are sensor-relative; RGB starts at active margins."""
    w, h = int(s.width), int(s.height)
    if w <= 0 or h <= 0:
        raise ValueError('RAW 有效画面尺寸无效')
    x, y = int(s.crop_left_margin), int(s.crop_top_margin)
    cw, ch = int(s.crop_width), int(s.crop_height)
    if (x == 65535 or y == 65535 or min(x, y) < 0 or min(cw, ch) <= 0 or
            x + cw > s.raw_width or y + ch > s.raw_height):
        return 0, 0, w, h
    left, top = int(s.left_margin), int(s.top_margin)
    x0, y0 = max(0, x - left), max(0, y - top)
    x1, y1 = min(w, x + cw - left), min(h, y + ch - top)
    if x1 <= x0 or y1 <= y0:
        return 0, 0, w, h
    return x0, y0, x1 - x0, y1 - y0


def orient(a, flip):
    """All eight LibRaw flip bit patterns, including mirrored orientations."""
    if flip not in range(8):
        raise ValueError('RAW 方向标记无效')
    if flip & 4:
        a = a.swapaxes(0, 1)
    if flip & 2:
        a = a[:, ::-1] if flip & 4 else a[::-1]
    if flip & 1:
        a = a[::-1] if flip & 4 else a[:, ::-1]
    return a


def resize_linear(a, size, box=None):
    """Float-channel resize: no intermediate 8-bit quantization."""
    if box is None and (a.shape[1], a.shape[0]) == size:
        return a
    return np.stack([np.asarray(Image.fromarray(a[:, :, c]).resize(
        size, Image.Resampling.BILINEAR, box=box)) for c in range(3)], axis=2)


def raw_pixels(rgb, sizes, half):
    x, y, w, h = raw_crop(sizes)
    factor = 2 if half else 1
    expected = ((sizes.height + factor - 1) // factor, (sizes.width + factor - 1) // factor)
    if rgb.shape[:2] != expected:
        raise ValueError('RAW 解码尺寸与有效区域不一致；此 RAW 几何格式暂未支持')
    a = rgb.astype(np.float32)
    a *= np.float32(1 / 65535)
    if half:
        # Fractional edges preserve odd inset origins instead of shifting by // 2.
        box = (x / 2, y / 2, (x + w) / 2, (y + h) / 2)
        if all(v.is_integer() for v in box):
            x0, y0, x1, y1 = map(int, box)
            a = a[y0:y1, x0:x1]
        else:
            a = resize_linear(a, (max(1, round(w / 2)), max(1, round(h / 2))), box)
    else:
        a = a[y:y + h, x:x + w]
    return orient(a, int(sizes.flip))


def rgb_matrix(chroma):
    """Linear RGB -> XYZ from PNG cHRM xy values; adapted to D65 for sRGB."""
    if len(chroma) != 8 or any(not math.isfinite(v) for v in chroma):
        raise ValueError('PNG 色度坐标无效')
    white, r, g, b = np.array(chroma, dtype=np.float64).reshape(4, 2)
    if any(y <= 0 for _, y in (white, r, g, b)):
        raise ValueError('PNG 色度坐标无效')
    def xyz(xy):
        x, y = xy
        return np.array([x / y, 1, (1 - x - y) / y])
    primaries = np.stack([xyz(r), xyz(g), xyz(b)], axis=1)
    try:
        matrix = primaries * np.linalg.solve(primaries, xyz(white))
    except np.linalg.LinAlgError as e:
        raise ValueError('PNG 色度坐标不可逆') from e
    bradford = np.array([[.8951, .2664, -.1614], [-.7502, 1.7135, .0367], [.0389, -.0685, 1.0296]])
    adapt = np.linalg.solve(bradford, np.diag((bradford @ xyz(SRGB_XY[:2])) /
                                             (bradford @ xyz(white))) @ bradford)
    return adapt @ matrix


SRGB_TO_XYZ = rgb_matrix(SRGB_XY)


def _exif_space(im):
    exif = im.getexif()
    try:
        nested = exif.get_ifd(34665)
        interop = nested.get(40965, {})
    except (KeyError, TypeError, ValueError):
        nested, interop = {}, {}
    space = nested.get(40961, exif.get(40961))
    return space, interop.get(1) if isinstance(interop, dict) else None


def raster_linear(source):
    """Return float32 linear sRGB and an explicit input-colour policy/warnings."""
    bitdepth = None
    if source.format == 'PNG' and getattr(source, 'fp', None):
        position = source.fp.tell()
        source.fp.seek(24); bitdepth = source.fp.read(1)
        source.fp.seek(position)
    info = dict(source.info)
    exif_space, interop = _exif_space(source)
    im = ImageOps.exif_transpose(source)
    warnings = []
    mode = im.mode
    if bitdepth == b'\x10' and mode not in ('I;16', 'I;16B', 'I', 'L'):
        raise ValueError('16 位 RGB PNG 暂不支持无损色彩解码，请使用 NEF 或 8 位 PNG')
    alpha = None
    if mode in ('RGBA', 'LA', 'P', 'PA') and ('A' in im.getbands() or 'transparency' in info or mode == 'PA'):
        alpha = np.asarray(im.convert('RGBA').getchannel('A'), dtype=np.float32) / 255
    profile = info.get('icc_profile')
    if 'icc_profile' in info and not profile:
        raise ValueError('嵌入的 ICC 配置为空或损坏')
    if profile:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            space = src.profile.xcolor_space.strip()
            if space == 'RGB' and mode in ('RGB', 'RGBA', 'P', 'PA', 'L', 'LA', '1'):
                color = im.convert('RGB')
            elif space == 'GRAY' and mode in ('L', 'LA', '1'):
                color = im.convert('L')
            elif space == 'CMYK' and mode == 'CMYK':
                color = im
            else:
                raise ValueError('ICC 色彩空间与图像通道不匹配，无法可靠转换')
            converted = ImageCms.profileToProfile(color, src, SRGB, outputMode='RGB',
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
            a = srgb_to_linear(np.asarray(converted, dtype=np.float32) / 255)
            policy = 'ICC → sRGB'
        except (ImageCms.PyCMSError, OSError, TypeError) as e:
            raise ValueError('嵌入的 ICC 配置损坏或不受支持，未猜测其色彩空间') from e
    else:
        if mode == 'CMYK':
            raise ValueError('CMYK 文件缺少 ICC 配置，无法确定印刷色彩空间；请嵌入正确 ICC 后导入')
        if mode in ('I;16', 'I;16B', 'I'):
            gray = np.asarray(im, dtype=np.float32)
            if gray.min() < 0 or gray.max() > 65535:
                raise ValueError('不支持此整数图像范围')
            encoded = np.repeat((gray / 65535)[..., None], 3, axis=2)
        else:
            encoded = np.asarray(im.convert('RGB'), dtype=np.float32) / 255
        gamma = info.get('gamma')
        chroma = info.get('chromaticity')
        if 'srgb' in info:
            a, policy = srgb_to_linear(encoded), 'PNG sRGB'
        elif gamma is not None or chroma is not None:
            if gamma is None or not math.isfinite(gamma) or not 0 < gamma <= 10:
                raise ValueError('PNG gAMA/cHRM 不完整或无效，无法可靠转换')
            linear = encoded ** np.float32(1 / gamma)
            matrix = np.linalg.solve(SRGB_TO_XYZ, rgb_matrix(chroma or SRGB_XY))
            a = (linear @ matrix.astype(np.float32).T).astype(np.float32)
            policy = 'PNG gAMA/cHRM → sRGB'
        elif exif_space == 2 or interop in ('R03', b'R03'):
            matrix = np.linalg.solve(SRGB_TO_XYZ, rgb_matrix(ADOBE_XY))
            a = encoded ** np.float32(563 / 256) @ matrix.astype(np.float32).T
            policy = 'EXIF Adobe RGB → sRGB'
        else:
            a, policy = srgb_to_linear(encoded), 'sRGB（无 ICC）'
            if exif_space != 1:
                warnings.append('文件无 ICC；按 sRGB 解释，无法确认原始色彩空间')
    if alpha is not None:
        a *= alpha[..., None]
        a += (1 - alpha)[..., None]
        warnings.append('透明区域已在线性光空间合成到白色背景')
    return a.astype(np.float32, copy=False), policy, warnings
