from pathlib import Path

import fitz

from backend.engine.analyzer.document_analyzer import analyze_document
from backend.engine.router.router import build_processing_plan
from backend.engine.router.models import StrategyKind
from backend.engine.strategies.stream_remove import StreamRemoveStrategy
from tests.fixtures_factory import make_vector_overlay_pdf


def _page_texts(path: Path) -> list[str]:
    doc = fitz.open(path)
    try:
        return [page.get_text("text") for page in doc]
    finally:
        doc.close()


def test_stream_remove_preserves_base_content_and_geometry(tmp_path):
    source = make_vector_overlay_pdf(tmp_path / "source.pdf")
    output = tmp_path / "clean.pdf"
    profile = analyze_document(source)
    plan = build_processing_plan(profile)
    assert plan.strategy is StrategyKind.STREAM_REMOVE

    before = fitz.open(source)
    try:
        before_rects = [tuple(page.rect) for page in before]
    finally:
        before.close()

    result = StreamRemoveStrategy().execute(source, output, plan)

    assert result.removed_items >= 1
    assert result.rasterized_pages == 0
    texts = _page_texts(output)
    assert all("Base lesson content page" in text for text in texts)
    assert all("TAILIEUONTHI.NET" not in text for text in texts)

    after = fitz.open(output)
    try:
        assert after.page_count == 3
        assert [tuple(page.rect) for page in after] == before_rects
    finally:
        after.close()


def test_stream_remove_does_not_remove_unrelated_repeated_content(tmp_path):
    source = tmp_path / "safe.pdf"
    output = tmp_path / "safe-clean.pdf"
    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "Repeated legitimate lesson heading", fontsize=14)
        page.insert_text((330, 760), "TAILIEUONTHI.NET", fontsize=12, color=(0.7, 0.7, 0.7))
    doc.save(source)
    doc.close()

    plan = build_processing_plan(analyze_document(source))
    result = StreamRemoveStrategy().execute(source, output, plan)
    texts = _page_texts(output)

    assert result.rasterized_pages == 0
    assert all("Repeated legitimate lesson heading" in text for text in texts)
    assert all("TAILIEUONTHI.NET" not in text for text in texts)
