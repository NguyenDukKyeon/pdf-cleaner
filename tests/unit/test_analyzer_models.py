import pytest

from backend.engine.analyzer.models import (
    DocumentKind,
    DocumentProfile,
    PageEvidence,
    WatermarkCandidate,
)


def test_document_profile_confidence_is_bounded():
    with pytest.raises(ValueError):
        DocumentProfile(page_count=1, kind=DocumentKind.RASTER, confidence=1.1)


def test_document_profile_rejects_non_positive_page_count():
    with pytest.raises(ValueError):
        DocumentProfile(page_count=0, kind=DocumentKind.RASTER, confidence=0.5)


def test_page_evidence_validates_page_index_and_ratios():
    with pytest.raises(ValueError):
        PageEvidence(page_index=-1)
    with pytest.raises(ValueError):
        PageEvidence(page_index=0, text_coverage=1.01)


def test_watermark_candidate_confidence_is_bounded():
    with pytest.raises(ValueError):
        WatermarkCandidate(kind="text", confidence=-0.01)
