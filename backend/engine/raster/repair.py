from __future__ import annotations

import cv2
import numpy as np

from .consensus import RasterWatermarkModel
from .text_guard import build_dark_content_guard


def _estimate_background(gray: np.ndarray, *, max_dimension: int = 900) -> np.ndarray:
    """Estimate local paper background on a bounded working image.

    A 300-DPI A4 page can contain millions of pixels and the old large-kernel
    full-resolution Gaussian blur dominated runtime and temporary RAM. Background
    is low-frequency information, so estimate it on a bounded image and upscale
    once. Repair itself remains at native page resolution.
    """

    height, width = gray.shape
    largest = max(height, width)
    if largest > max_dimension:
        scale = max_dimension / float(largest)
        small_width = max(1, int(round(width * scale)))
        small_height = max(1, int(round(height * scale)))
        working = cv2.resize(gray, (small_width, small_height), interpolation=cv2.INTER_AREA)
    else:
        working = gray

    kernel = max(15, int(round(min(working.shape) * 0.08)))
    kernel = min(kernel, 81)
    if kernel % 2 == 0:
        kernel += 1
    background = cv2.GaussianBlur(working, (kernel, kernel), 0)
    if background.shape != gray.shape:
        background = cv2.resize(background, (width, height), interpolation=cv2.INTER_LINEAR)
    return background.astype(np.float32, copy=False)


def repair_with_model(image: np.ndarray, model: RasterWatermarkModel) -> tuple[np.ndarray, int]:
    arr = np.asarray(image).copy()
    if arr.shape[:2] != model.mask.shape:
        raise ValueError("image geometry must match watermark model")
    if arr.ndim == 2:
        rgb = np.repeat(arr[..., None], 3, axis=2)
        grayscale_input = True
    elif arr.ndim == 3 and arr.shape[2] >= 3:
        rgb = arr[..., :3].astype(np.uint8, copy=True)
        grayscale_input = False
    else:
        raise ValueError("image must be grayscale or RGB/RGBA")

    guard = build_dark_content_guard(rgb, dark_threshold=105, dilate_px=1)
    repair_mask = model.mask & ~guard
    if not repair_mask.any():
        return arr, 0

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    local_bg = _estimate_background(gray)

    # Compute increments only for watermark pixels instead of allocating a
    # full-page HxWx3 float32 copy and full-page delta tensor.
    source_gray = gray[repair_mask]
    target = np.clip(
        np.maximum(local_bg[repair_mask] + 4.0, source_gray + 24.0),
        0,
        255,
    )
    increment = target - source_gray
    out = rgb.copy()
    repaired = out[repair_mask].astype(np.float32)
    repaired += increment[:, None]
    out[repair_mask] = np.clip(repaired, 0, 255).astype(np.uint8)

    changed = int(np.count_nonzero(np.any(out != rgb, axis=2)))
    if grayscale_input:
        return cv2.cvtColor(out, cv2.COLOR_RGB2GRAY), changed
    if arr.ndim == 3 and arr.shape[2] == 4:
        result = arr.copy()
        result[..., :3] = out
        return result, changed
    return out, changed
