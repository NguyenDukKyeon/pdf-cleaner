from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DocumentKind(str, Enum):
    VECTOR = "vector"
    HYBRID = "hybrid"
    RASTER = "raster"


def _validate_unit_interval(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1 inclusive")


@dataclass(frozen=True, slots=True)
class PageEvidence:
    page_index: int
    text_coverage: float = 0.0
    full_page_image_coverage: float = 0.0
    text_char_count: int = 0
    stream_hashes: tuple[str, ...] = ()
    image_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("page_index must be non-negative")
        _validate_unit_interval("text_coverage", self.text_coverage)
        _validate_unit_interval("full_page_image_coverage", self.full_page_image_coverage)
        if self.text_char_count < 0:
            raise ValueError("text_char_count must be non-negative")


@dataclass(frozen=True, slots=True)
class WatermarkCandidate:
    kind: str
    confidence: float
    marker: str | None = None
    page_indices: tuple[int, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("kind must not be empty")
        _validate_unit_interval("confidence", self.confidence)
        if any(index < 0 for index in self.page_indices):
            raise ValueError("page_indices must be non-negative")


@dataclass(frozen=True, slots=True)
class DocumentProfile:
    page_count: int
    kind: DocumentKind
    confidence: float
    sampled_pages: tuple[int, ...] = ()
    page_evidence: tuple[PageEvidence, ...] = ()
    watermark_candidates: tuple[WatermarkCandidate, ...] = ()
    text_layer_ratio: float = 0.0
    full_page_image_ratio: float = 0.0

    def __post_init__(self) -> None:
        if self.page_count <= 0:
            raise ValueError("page_count must be positive")
        _validate_unit_interval("confidence", self.confidence)
        _validate_unit_interval("text_layer_ratio", self.text_layer_ratio)
        _validate_unit_interval("full_page_image_ratio", self.full_page_image_ratio)
        if any(index < 0 or index >= self.page_count for index in self.sampled_pages):
            raise ValueError("sampled_pages must reference pages in the document")
