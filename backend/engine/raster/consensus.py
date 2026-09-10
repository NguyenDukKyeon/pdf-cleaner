from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class RasterWatermarkModel:
    mask: np.ndarray
    template_gray: np.ndarray
    confidence: float
    sample_count: int
    marker_id: str | None = None

    def __post_init__(self) -> None:
        if self.mask.ndim != 2 or self.mask.dtype != np.bool_:
            raise ValueError("mask must be a 2D bool array")
        if self.template_gray.shape != self.mask.shape:
            raise ValueError("template_gray must match mask shape")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.sample_count < 2:
            raise ValueError("at least two samples are required")


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.astype(np.uint8, copy=False)
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError("samples must be grayscale, RGB or RGBA images")
    rgb = image[..., :3].astype(np.uint8, copy=False)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _tailieuonthi_prior(h: int, w: int) -> np.ndarray:
    x1, y1 = 0.50 * w, 0.98 * h
    x2, y2 = 0.98 * w, 0.52 * h
    yy, xx = np.mgrid[0:h, 0:w]
    vx, vy = x2 - x1, y2 - y1
    denom = max(1.0, float((vx * vx + vy * vy) ** 0.5))
    dist = np.abs(vy * (xx - x1) - vx * (yy - y1)) / denom
    band = max(10.0, 0.11 * min(h, w))
    return (xx >= 0.42 * w) & (yy >= 0.42 * h) & (dist <= band)


def learn_watermark_model(samples: Sequence[np.ndarray] | Iterable[np.ndarray], candidates=None, signature_registry=None, *, marker_id: str | None = None) -> RasterWatermarkModel:
    sample_list = [np.asarray(item) for item in samples]
    if len(sample_list) < 2:
        raise ValueError("at least two samples are required")
    h, w = sample_list[0].shape[:2]
    if h <= 0 or w <= 0:
        raise ValueError("empty image sample")
    normalized = []
    for image in sample_list:
        if image.shape[:2] != (h, w):
            image = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
        normalized.append(_to_gray(image).astype(np.float32))
    stack = np.stack(normalized, axis=0)
    median = np.median(stack, axis=0)
    mad = np.median(np.abs(stack - median[None, ...]), axis=0)
    k = max(15, int(round(min(h, w) * 0.10)))
    if k % 2 == 0:
        k += 1
    background = cv2.GaussianBlur(median, (k, k), 0)
    local_darkening = background - median
    mask = (mad <= 5.5) & (median >= 125.0) & (median <= 246.0) & (local_darkening >= 4.0)
    selected_marker = marker_id
    if selected_marker is None and candidates:
        try:
            selected_marker = next((c.marker for c in candidates if getattr(c, "marker", None)), None)
        except TypeError:
            selected_marker = None
    if selected_marker == "tailieuonthi":
        mask &= _tailieuonthi_prior(h, w)
    mask &= median >= 115.0
    mask_u8 = mask.astype(np.uint8) * 255
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)
    mask_u8 = cv2.dilate(mask_u8, np.ones((3, 3), np.uint8), iterations=1)
    mask = mask_u8 > 0
    pixels = int(mask.sum())
    support = pixels / float(h * w)
    if pixels:
        stability = float(np.clip(1.0 - np.mean(mad[mask]) / 12.0, 0.0, 1.0))
        confidence = float(np.clip(0.55 + 0.30 * stability + 0.15 * min(1.0, support / 0.02), 0.0, 0.98))
    else:
        confidence = 0.0
    return RasterWatermarkModel(mask=mask, template_gray=median, confidence=confidence, sample_count=len(sample_list), marker_id=selected_marker)
