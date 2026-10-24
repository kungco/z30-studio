"""Non-generative portrait retouch foundation. Input/output: RGB uint8 arrays.
Optional dependencies: opencv-python, mediapipe. No facial reshaping in this module.
"""
from dataclasses import dataclass
import numpy as np
import cv2

@dataclass(frozen=True)
class RetouchSettings:
    smoothing: float = 0.25
    blemish_strength: float = 0.35
    brighten: float = 0.08

def _clamp(v):
    return float(np.clip(v, 0.0, 1.0))

def retouch_rgb(rgb: np.ndarray, skin_mask: np.ndarray,
                settings: RetouchSettings = RetouchSettings()) -> np.ndarray:
    """Texture-conscious, mask-limited skin retouch; never modifies source."""
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("rgb must be HxWx3 uint8")
    if skin_mask.shape != rgb.shape[:2]:
        raise ValueError("skin_mask shape must match image")
    if not np.isfinite(skin_mask).all():
        raise ValueError("skin_mask contains non-finite values")
    mask = np.clip(skin_mask.astype(np.float32), 0, 1)
    mask = cv2.GaussianBlur(mask, (0, 0), 2.0)
    mask = mask[..., None]
    smooth = _clamp(settings.smoothing)
    blemish = _clamp(settings.blemish_strength)
    bright = _clamp(settings.brighten)
    base = rgb.astype(np.float32)
    # Separate coarse color from texture; preserve high-frequency detail.
    low = cv2.bilateralFilter(rgb, 9, 28 + 35 * smooth, 7).astype(np.float32)
    # Gentle median correction reduces isolated spots; strong acne requires
    # explicit spot masks and inpainting, not indiscriminate face blur.
    median = cv2.medianBlur(rgb, 5).astype(np.float32)
    corrected = low * (1 - blemish * 0.3) + median * (blemish * 0.3)
    result = base * (1 - mask * smooth) + corrected * (mask * smooth)
    result = result + mask * (bright * 12.0)
    return np.clip(np.rint(result), 0, 255).astype(np.uint8)

def inpaint_spots(rgb: np.ndarray, spot_mask: np.ndarray, radius: float = 3.0):
    """Inpaint only explicitly marked blemishes, preserving surrounding skin."""
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("rgb must be HxWx3 uint8")
    if spot_mask.shape != rgb.shape[:2]:
        raise ValueError("spot_mask shape must match image")
    mask = np.where(spot_mask > 0, 255, 0).astype(np.uint8)
    return cv2.inpaint(rgb, mask, float(np.clip(radius, 1, 15)), cv2.INPAINT_TELEA)
