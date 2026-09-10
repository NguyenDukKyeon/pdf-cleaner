from __future__ import annotations

import cv2
import numpy as np


def build_dark_content_guard(image: np.ndarray, *, dark_threshold: int = 105, dilate_px: int = 1) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 2:
        gray = arr.astype(np.uint8, copy=False)
    elif arr.ndim == 3 and arr.shape[2] >= 3:
        gray = cv2.cvtColor(arr[..., :3].astype(np.uint8, copy=False), cv2.COLOR_RGB2GRAY)
    else:
        raise ValueError("image must be grayscale or RGB/RGBA")
    guard = gray <= int(dark_threshold)
    if dilate_px > 0:
        k = 2 * int(dilate_px) + 1
        guard = cv2.dilate(guard.astype(np.uint8), np.ones((k, k), np.uint8), iterations=1) > 0
    return guard
