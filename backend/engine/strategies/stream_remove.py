from __future__ import annotations

from pathlib import Path
from typing import Callable

from backend.engine.core_stream import clean_pdf_stream_safe
from backend.engine.router.models import ProcessingPlan, StrategyKind
from backend.engine.signatures.registry import SignatureRegistry

from .base import StrategyResult


class StreamRemoveStrategy:
    def __init__(self, registry: SignatureRegistry | None = None):
        self._registry = registry or SignatureRegistry.load_default()

    def _markers_for_plan(self, plan: ProcessingPlan) -> list[str]:
        marker_ids = {operation.marker for operation in plan.operations if operation.marker}
        aliases: list[str] = []
        for signature in self._registry.signatures:
            if signature.id in marker_ids:
                aliases.extend(signature.aliases)
        return aliases

    def execute(
        self,
        input_pdf: Path,
        output_pdf: Path,
        plan: ProcessingPlan,
        *,
        log: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> StrategyResult:
        if plan.strategy is not StrategyKind.STREAM_REMOVE:
            raise ValueError("StreamRemoveStrategy requires a STREAM_REMOVE plan")
        markers = self._markers_for_plan(plan)
        if not markers:
            raise ValueError("STREAM_REMOVE plan has no known watermark signature markers")
        result = clean_pdf_stream_safe(
            input_pdf=input_pdf,
            output_pdf=output_pdf,
            markers=markers,
            allow_stream_auto=False,
            log=log,
            should_cancel=should_cancel,
        )
        return StrategyResult(
            removed_items=int(result["removed_streams"]),
            changed_pages=int(result["changed_pages"]),
            rasterized_pages=int(result.get("raster_pages", 0)),
            saved_to=str(result["saved_to"]),
        )
