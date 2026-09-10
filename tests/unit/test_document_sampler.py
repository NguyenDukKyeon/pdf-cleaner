import pytest

from backend.engine.analyzer.document_analyzer import select_sample_pages


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
