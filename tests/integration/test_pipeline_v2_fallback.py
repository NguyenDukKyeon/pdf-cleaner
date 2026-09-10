from pathlib import Path
import fitz
import pytest

from backend.engine.router.models import ProcessingPlan, StrategyKind
from backend.engine.pipeline_v2.execute import execute_plan
from backend.engine.strategies.base import StrategyResult


class BrokenStrategy:
    def execute(self, input_pdf, output_pdf, plan, **kwargs):
        Path(output_pdf).write_bytes(b'corrupt partial')
        raise RuntimeError('synthetic V2 failure')


class CopyLegacy:
    def execute(self, input_pdf, output_pdf, plan, **kwargs):
        Path(output_pdf).write_bytes(Path(input_pdf).read_bytes())
        return StrategyResult(saved_to=str(output_pdf))


def _pdf(path: Path):
    doc = fitz.open(); p = doc.new_page(); p.insert_text((72,72), 'BASE CONTENT'); doc.save(path); doc.close()


def test_v2_failure_falls_back_without_exposing_partial_output(tmp_path):
    src = tmp_path/'src.pdf'; out = tmp_path/'out.pdf'; _pdf(src)
    plan = ProcessingPlan(StrategyKind.STREAM_REMOVE, .96)
    report = execute_plan(src, out, plan, strategy=BrokenStrategy(), legacy_strategy=CopyLegacy(), allow_legacy_fallback=True)
    assert report.used_fallback is True
    assert out.read_bytes().startswith(b'%PDF')
    assert b'corrupt partial' not in out.read_bytes()
    assert not list(tmp_path.glob('*.v2tmp*.pdf'))


def test_v2_failure_without_fallback_keeps_existing_final_untouched(tmp_path):
    src = tmp_path/'src.pdf'; out = tmp_path/'out.pdf'; _pdf(src)
    out.write_bytes(b'existing-final')
    plan = ProcessingPlan(StrategyKind.STREAM_REMOVE, .96)
    with pytest.raises(RuntimeError, match='synthetic V2 failure'):
        execute_plan(src, out, plan, strategy=BrokenStrategy(), allow_legacy_fallback=False)
    assert out.read_bytes() == b'existing-final'
    assert not list(tmp_path.glob('*.v2tmp*.pdf'))
