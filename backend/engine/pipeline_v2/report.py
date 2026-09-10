from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass(frozen=True, slots=True)
class ProcessingReport:
    strategy: str
    confidence: float
    output_path: str
    worker_count: int = 1
    used_fallback: bool = False
    fallback_reason: str | None = None
    changed_pages: int = 0
    removed_items: int = 0
    rasterized_pages: int = 0
    native_image_pages: int = 0
    ocr_calls: int = 0
    analysis_seconds: float = 0.0
    processing_seconds: float = 0.0
    qc_seconds: float = 0.0
    total_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
