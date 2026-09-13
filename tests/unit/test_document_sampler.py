import fitz
import pytest

from backend.engine.analyzer.document_analyzer import (
    analyze_document,
    select_sample_pages,
    select_staged_sample_pages,
)


def test_select_sample_pages_spreads_samples_across_document():
    assert select_sample_pages(20, max_samples=5) == (0, 5, 10, 14, 19)


def test_select_sample_pages_returns_all_pages_when_document_is_small():
    assert select_sample_pages(4, max_samples=8) == (0, 1, 2, 3)


def test_select_sample_pages_is_stable_and_unique():
    result = select_sample_pages(100, max_samples=8)
    assert result[0] == 0
    assert result[-1] == 99
    assert len(result) == 8
    assert len(set(result)) == 8
    assert result == tuple(sorted(result))


@pytest.mark.parametrize("page_count,max_samples", [(0, 8), (-1, 8), (1, 0), (1, -1)])
def test_select_sample_pages_rejects_invalid_arguments(page_count, max_samples):
    with pytest.raises(ValueError):
        select_sample_pages(page_count, max_samples=max_samples)


def test_select_staged_sample_pages_hierarchical_subsets():
    stages = select_staged_sample_pages(20)
    assert len(stages[0]) == 3
    assert len(stages[1]) == 5
    assert len(stages[2]) == 8
    assert set(stages[0]).issubset(stages[1])
    assert set(stages[1]).issubset(stages[2])
    assert select_staged_sample_pages(2) == ((0, 1),)


@pytest.mark.parametrize("page_count,stages", [(0, (3, 5, 8)), (-1, (3, 5, 8)), (10, ()), (10, (0, 5))])
def test_select_staged_sample_pages_rejects_invalid_arguments(page_count, stages):
    with pytest.raises(ValueError):
        select_staged_sample_pages(page_count, stages=stages)


def test_staged_evidence_collection_early_stops_and_expands(tmp_path, monkeypatch):
    import backend.engine.analyzer.document_analyzer as analyzer_mod
    from tests.fixtures_factory import make_vector_overlay_pdf

    high_conf_pdf = make_vector_overlay_pdf(tmp_path / "high_conf.pdf", pages=20)

    ambig_pdf = tmp_path / "ambig.pdf"
    doc = fitz.open()
    for i in range(20):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Variable content without repeated watermark {i}")
    doc.save(ambig_pdf)
    doc.close()

    inspected_pages = []
    orig_collect = analyzer_mod._collect_page_evidence

    def spy_collect(doc, page_index):
        inspected_pages.append(page_index)
        return orig_collect(doc, page_index)

    monkeypatch.setattr(analyzer_mod, "_collect_page_evidence", spy_collect)

    inspected_pages.clear()
    profile_high = analyze_document(high_conf_pdf)
    assert len(inspected_pages) == 3
    assert len(profile_high.sampled_pages) == 3
    assert set(inspected_pages) == set(profile_high.sampled_pages)
    assert profile_high.confidence >= 0.95

    inspected_pages.clear()
    profile_ambig = analyze_document(ambig_pdf)
    assert len(inspected_pages) == 8
    assert len(profile_ambig.sampled_pages) == 8
    assert set(inspected_pages) == set(profile_ambig.sampled_pages)

