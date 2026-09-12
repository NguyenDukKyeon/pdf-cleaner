from __future__ import annotations

from typing import Any, Mapping
import numpy as np

from .config import ProcessorConfig


class BaseProcessor:
    """Common processor interface for every watermark mode."""

    mode: str = "base"

    def __init__(self, config: ProcessorConfig | Mapping[str, Any] | None = None):
        if config is None:
            config = ProcessorConfig(mode=self.mode)
        elif isinstance(config, Mapping):
            config = ProcessorConfig(mode=self.mode, params=dict(config))
        self.config: ProcessorConfig = config

    def process_page(self, page_rgb: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def __call__(self, page_rgb: np.ndarray) -> np.ndarray:
        return self.process_page(page_rgb)
