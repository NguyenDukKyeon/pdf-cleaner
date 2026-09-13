from pathlib import Path
import sys

ENGINE_DIR = Path(__file__).resolve().parents[2] / "backend" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
from tdm_cleaner.core import worker_pool


def test_auto_worker_selection_can_exceed_one(monkeypatch):
    monkeypatch.setattr(worker_pool.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(worker_pool, "memory_safe_worker_limit", lambda dpi: 6)
    assert worker_pool.auto_worker_count(0, page_count=20, dpi=240) == 6


def test_auto_worker_selection_respects_memory_page_and_request(monkeypatch):
    monkeypatch.setattr(worker_pool.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(worker_pool, "memory_safe_worker_limit", lambda dpi: 2)
    assert worker_pool.auto_worker_count(0, page_count=20, dpi=240) == 2
    assert worker_pool.auto_worker_count(8, page_count=1, dpi=240) == 1


def test_execute_plan_passes_workers_to_raster_template_strategy(tmp_path):
    import fitz
    from backend.engine.pipeline_v2.execute import execute_plan
    from backend.engine.router.models import ProcessingPlan, StrategyKind, ProcessingOperation
    from backend.engine.strategies.base import StrategyResult
    from backend.engine.strategies.raster_template import RasterTemplateStrategy

    doc = fitz.open()
    for _ in range(4):
        doc.new_page(width=100, height=100)
    pdf_path = tmp_path / "doc.pdf"
    out_path = tmp_path / "doc_out.pdf"
    doc.save(pdf_path)
    doc.close()

    received_kwargs = {}

    class MockRasterStrategy(RasterTemplateStrategy):
        def execute(self, input_pdf, output_pdf, plan, **kwargs):
            received_kwargs.update(kwargs)
            output_pdf.write_bytes(b"%PDF-1.4 mock")
            return StrategyResult(
                removed_items=1,
                changed_pages=1,
                rasterized_pages=0,
                saved_to=str(output_pdf),
                native_image_pages=4,
                ocr_calls=0,
            )

    plan = ProcessingPlan(
        strategy=StrategyKind.RASTER_TEMPLATE,
        confidence=0.9,
        operations=(ProcessingOperation("test", "tailieuonthi"),),
        requires_strict_qc=True,
    )

    execute_plan(
        pdf_path,
        out_path,
        plan,
        strategy=MockRasterStrategy(),
        requested_workers=3,
        dpi=240,
    )

    assert "workers" in received_kwargs
    assert received_kwargs["workers"] >= 1


def test_execute_plan_safe_preset_worker_count_is_one(tmp_path):
    import fitz
    from backend.engine.pipeline_v2.execute import execute_plan
    from backend.engine.router.models import ProcessingPlan, StrategyKind, ProcessingOperation
    from backend.engine.strategies.base import StrategyResult
    from backend.engine.strategies.raster_template import RasterTemplateStrategy

    doc = fitz.open()
    for _ in range(4):
        doc.new_page(width=100, height=100)
    pdf_path = tmp_path / "doc.pdf"
    out_path = tmp_path / "doc_out.pdf"
    doc.save(pdf_path)
    doc.close()

    received_kwargs = {}

    class MockRasterStrategy(RasterTemplateStrategy):
        def execute(self, input_pdf, output_pdf, plan, **kwargs):
            received_kwargs.update(kwargs)
            output_pdf.write_bytes(b"%PDF-1.4 mock")
            return StrategyResult(
                removed_items=1,
                changed_pages=1,
                rasterized_pages=0,
                saved_to=str(output_pdf),
                native_image_pages=4,
                ocr_calls=0,
            )

    plan = ProcessingPlan(
        strategy=StrategyKind.RASTER_TEMPLATE,
        confidence=0.9,
        operations=(ProcessingOperation("test", "tailieuonthi"),),
        requires_strict_qc=True,
    )

    # Safe preset explicitly requests 1 worker
    execute_plan(
        pdf_path,
        out_path,
        plan,
        strategy=MockRasterStrategy(),
        requested_workers=1,
        dpi=240,
    )

    assert received_kwargs.get("workers") == 1
