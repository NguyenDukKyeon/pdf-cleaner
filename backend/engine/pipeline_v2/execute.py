from __future__ import annotations
import os
from pathlib import Path
import time
import uuid
import sys
import fitz
from backend.engine.router.models import ProcessingPlan, StrategyKind
from backend.engine.strategies.legacy import LegacyStrategy
from backend.engine.strategies.raster_template import RasterTemplateStrategy
from backend.engine.strategies.stream_remove import StreamRemoveStrategy

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
from tdm_cleaner.core.worker_pool import auto_worker_count
from .report import ProcessingReport


def _default_strategy(plan: ProcessingPlan):
    if plan.strategy is StrategyKind.STREAM_REMOVE: return StreamRemoveStrategy()
    if plan.strategy is StrategyKind.RASTER_TEMPLATE: return RasterTemplateStrategy()
    if plan.strategy is StrategyKind.LEGACY: return LegacyStrategy()
    raise NotImplementedError(f'V2 strategy not implemented yet: {plan.strategy.value}')

def _temp_path(output_path: Path) -> Path:
    return output_path.with_name(f'{output_path.stem}.v2tmp_{uuid.uuid4().hex[:8]}.pdf')

def _safe_unlink(path: Path) -> None:
    try: path.unlink(missing_ok=True)
    except Exception: pass

def execute_plan(input_path: str | Path, output_path: str | Path, plan: ProcessingPlan, callbacks: dict | None = None, *, strategy=None, legacy_strategy=None, allow_legacy_fallback: bool = True, requested_workers: int = 0, dpi: int = 240, output_dpi: int = 240, quality: int = 92) -> ProcessingReport:
    input_path = Path(input_path).expanduser().resolve(); output_path = Path(output_path).expanduser().resolve(); output_path.parent.mkdir(parents=True, exist_ok=True)
    callbacks = dict(callbacks or {}); log = callbacks.get('log'); progress = callbacks.get('progress'); should_cancel = callbacks.get('should_cancel')
    with fitz.open(str(input_path)) as doc: page_count = max(1, int(doc.page_count))
    workers = auto_worker_count(int(requested_workers or 0), page_count, int(dpi))
    started = time.perf_counter(); staging = _temp_path(output_path); used_fallback = False; fallback_reason = None; active_name = plan.strategy.value
    try:
        chosen = strategy if strategy is not None else _default_strategy(plan)
        try:
            kwargs = {'log':log,'progress':progress,'should_cancel':should_cancel}
            if isinstance(chosen, LegacyStrategy): kwargs.update(workers=workers,dpi=dpi,output_dpi=output_dpi,quality=quality)
            result = chosen.execute(input_path, staging, plan, **kwargs)
        except Exception as exc:
            _safe_unlink(staging)
            if not allow_legacy_fallback or plan.strategy is StrategyKind.LEGACY: raise
            used_fallback = True; fallback_reason = str(exc); active_name = StrategyKind.LEGACY.value
            fallback = legacy_strategy or LegacyStrategy(); kwargs = {'log':log,'progress':progress,'should_cancel':should_cancel}
            if isinstance(fallback, LegacyStrategy): kwargs.update(workers=workers,dpi=dpi,output_dpi=output_dpi,quality=quality)
            result = fallback.execute(input_path, staging, plan, **kwargs)
        if not staging.exists(): raise RuntimeError('strategy completed without creating staging output')
        os.replace(str(staging), str(output_path))
        processing_seconds = time.perf_counter() - started
        return ProcessingReport(strategy=active_name, confidence=plan.confidence, output_path=str(output_path), worker_count=workers, used_fallback=used_fallback, fallback_reason=fallback_reason, changed_pages=int(getattr(result,'changed_pages',0)), removed_items=int(getattr(result,'removed_items',0)), rasterized_pages=int(getattr(result,'rasterized_pages',0)), native_image_pages=int(getattr(result,'native_image_pages',0)), ocr_calls=int(getattr(result,'ocr_calls',0)), processing_seconds=processing_seconds, total_seconds=processing_seconds, metadata=dict(getattr(result,'metadata',{}) or {}))
    finally:
        _safe_unlink(staging)
