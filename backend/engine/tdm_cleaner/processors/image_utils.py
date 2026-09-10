from __future__ import annotations

from typing import Any
import cv2
import numpy as np


def ensure_rgb_uint8(page_rgb: np.ndarray) -> np.ndarray:
    """Normalize an input image to RGB uint8."""
    if not isinstance(page_rgb, np.ndarray):
        raise TypeError("page_rgb phải là numpy.ndarray.")
    if page_rgb.ndim == 2:
        page_rgb = cv2.cvtColor(page_rgb, cv2.COLOR_GRAY2RGB)
    if page_rgb.ndim != 3 or page_rgb.shape[2] != 3:
        raise ValueError("page_rgb phải là ảnh RGB 3 kênh hoặc grayscale 1 kênh.")
    if page_rgb.dtype != np.uint8:
        page_rgb = np.clip(page_rgb, 0, 255).astype(np.uint8)
    return page_rgb


def extract_image(result: Any) -> np.ndarray:
    """Extract image from legacy engine result: image or (image, masks, meta)."""
    image = result[0] if isinstance(result, tuple) else result
    return ensure_rgb_uint8(image)
