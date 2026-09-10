from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class StrategyResult:
    removed_items: int = 0
    changed_pages: int = 0
    rasterized_pages: int = 0
    saved_to: str = ""
    native_image_pages: int = 0
    ocr_calls: int = 0
