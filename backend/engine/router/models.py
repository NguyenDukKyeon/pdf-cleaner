from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StrategyKind(str, Enum):
    STREAM_REMOVE = "stream_remove"
    VECTOR_REMOVE = "vector_remove"
    RASTER_TEMPLATE = "raster_template"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True)
class ProcessingOperation:
    kind: str
    marker: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessingPlan:
    strategy: StrategyKind
    confidence: float
    operations: tuple[ProcessingOperation, ...] = ()
    requires_strict_qc: bool = True
    content_profile: str = "auto"
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1 inclusive")
