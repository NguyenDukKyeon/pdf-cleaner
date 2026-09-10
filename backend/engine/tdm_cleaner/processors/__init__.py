from .config import ProcessorConfig, DEFAULT_ENGINE_PATHS
from .base import BaseProcessor
from .adapters import (
    EbookProcessor,
    LyProcessor,
    ToanProcessor,
    HoaProcessor,
    process_ebook,
    process_ly,
    process_toan,
    process_hoa,
)
from .factory import PROCESSOR_REGISTRY, get_processor, process_by_mode, normalize_mode

__all__ = [
    "ProcessorConfig",
    "DEFAULT_ENGINE_PATHS",
    "BaseProcessor",
    "EbookProcessor",
    "LyProcessor",
    "ToanProcessor",
    "HoaProcessor",
    "process_ebook",
    "process_ly",
    "process_toan",
    "process_hoa",
    "PROCESSOR_REGISTRY",
    "get_processor",
    "process_by_mode",
    "normalize_mode",
]
