from __future__ import annotations

from typing import Any, Mapping
import numpy as np

from .base import BaseProcessor
from .config import ProcessorConfig
from .adapters import EbookProcessor, LyProcessor, ToanProcessor, HoaProcessor

PROCESSOR_REGISTRY: dict[str, type[BaseProcessor]] = {
    "ebook": EbookProcessor,
    "ly": LyProcessor,
    "ipclass": LyProcessor,
    "toan": ToanProcessor,
    "tdm": ToanProcessor,
    "hoa": HoaProcessor,
    "tyhh": HoaProcessor,
}


def normalize_mode(mode: str) -> str:
    key = str(mode).strip().lower()
    if key not in PROCESSOR_REGISTRY:
        raise ValueError(f"Mode không hợp lệ: {mode}. Hợp lệ: {sorted(PROCESSOR_REGISTRY)}")
    if key in {"ipclass"}:
        return "ly"
    if key in {"tdm"}:
        return "toan"
    if key in {"tyhh"}:
        return "hoa"
    return key


def get_processor(mode: str, config: Mapping[str, Any] | ProcessorConfig | None = None) -> BaseProcessor:
    """Create a processor adapter for one configured watermark mode."""
    key = normalize_mode(mode)
    cls = PROCESSOR_REGISTRY[key]
    cfg = config if isinstance(config, ProcessorConfig) else ProcessorConfig(mode=key, params=dict(config or {}))
    return cls(cfg)


def process_by_mode(
    mode: str,
    page_rgb: np.ndarray,
    config: Mapping[str, Any] | ProcessorConfig | None = None,
) -> np.ndarray:
    processor = get_processor(mode, config)
    return processor.process_page(page_rgb)
