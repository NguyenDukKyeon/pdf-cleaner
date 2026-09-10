from __future__ import annotations

import cv2
import numpy as np

from .consensus import RasterWatermarkModel
from .text_guard import build_dark_content_guard


def repair_with_model(image: np.ndarray, model: RasterWatermarkModel) -> tuple[np.ndarray, int]:
    arr = np.asarray(image).copy()
    if arr.shape[:2] != model.mask.shape:
        raise ValueError("image geometry must match watermark model")
    if arr.ndim == 2:
        rgb = np.repeat(arr[..., None], 3, axis=2); grayscale_input = True
    elif arr.ndim == 3 and arr.shape[2] >= 3:
        rgb = arr[..., :3].astype(np.uint8, copy=True); grayscale_input = False
    else:
        raise ValueError("image must be grayscale or RGB/RGBA")
    guard = build_dark_content_guard(rgb, dark_threshold=105, dilate_px=1)
    repair_mask = model.mask & ~guard
    if not repair_mask.any():
        return arr, 0
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    k = max(15, int(round(min(gray.shape) * 0.08)))
    if k % 2 == 0:
        k += 1
    local_bg = cv2.GaussianBlur(gray, (k, k), 0)
    target = np.clip(np.maximum(local_bg + 4.0, gray + 24.0), 0, 255)
    delta = (target - gray)[..., None]
    out = rgb.astype(np.float32)
    out[repair_mask] = np.clip(out[repair_mask] + delta[repair_mask], 0, 255)
    out = out.astype(np.uint8)
    changed = int(np.count_nonzero(np.any(out != rgb, axis=2)))
    if grayscale_input:
        return cv2.cvtColor(out, cv2.COLOR_RGB2GRAY), changed
    if arr.ndim == 3 and arr.shape[2] == 4:
        result = arr.copy(); result[..., :3] = out; return result, changed
    return out, changed
