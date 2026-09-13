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


def test_preset_quality_invariants_and_worker_requests():
    from backend.service import PRESET_ALIASES, _resolve_run_config

    expected = {
        "fast": {"dpi": 200, "output_dpi": 200, "quality": 88, "cpu": 0},
        "balanced": {"dpi": 240, "output_dpi": 240, "quality": 92, "cpu": 0},
        "high_quality": {"dpi": 320, "output_dpi": 320, "quality": 95, "cpu": 0},
        "safe_mode": {"dpi": 240, "output_dpi": 240, "quality": 95, "cpu": 1},
    }

    for name, values in expected.items():
        assert name in PRESET_ALIASES
        assert PRESET_ALIASES[name]["dpi"] == values["dpi"]
        assert PRESET_ALIASES[name]["output_dpi"] == values["output_dpi"]
        assert PRESET_ALIASES[name]["quality"] == values["quality"]
        assert PRESET_ALIASES[name]["cpu"] == values["cpu"]

        cfg = _resolve_run_config(name, {})
        assert cfg["quality_profile"] == name
        assert cfg["dpi"] == values["dpi"]
        assert cfg["output_dpi"] == values["output_dpi"]
        assert cfg["quality"] == values["quality"]
        assert cfg["cpu"] == values["cpu"]

    # Default selected preset remains balanced
    for fallback in ("", None, "invalid_name"):
        default_cfg = _resolve_run_config(fallback, {})
        assert default_cfg["quality_profile"] == "balanced"
        assert default_cfg["dpi"] == 240
        assert default_cfg["output_dpi"] == 240
        assert default_cfg["quality"] == 92
        assert default_cfg["cpu"] == 0


def test_footer_cleanup_default_remains_auto_for_all_presets():
    import inspect
    from backend.engine.pipeline_v2.execute import execute_plan
    from backend.engine.strategies.raster_template import RasterTemplateStrategy
    from backend.service import FOOTER_CLEANUPS, PRESET_ALIASES

    assert "auto" in FOOTER_CLEANUPS

    # Signature default for footer_cleanup must be 'auto'
    sig = inspect.signature(execute_plan)
    assert sig.parameters["footer_cleanup"].default == "auto"

    strat_sig = inspect.signature(RasterTemplateStrategy.execute)
    assert strat_sig.parameters["footer_cleanup"].default == "auto"

    # Presets definition must not override or disable footer_cleanup
    for preset_name in PRESET_ALIASES:
        assert "footer_cleanup" not in PRESET_ALIASES[preset_name]

