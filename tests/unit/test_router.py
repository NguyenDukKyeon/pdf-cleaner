import pytest

from backend.engine.analyzer.models import DocumentKind, DocumentProfile, WatermarkCandidate
from backend.engine.router.models import StrategyKind
from backend.engine.router.router import build_processing_plan


def profile(kind, *, confidence=1.0, image_ratio=0.0, candidate=None):
    candidates = () if candidate is None else (candidate,)
    return DocumentProfile(
        page_count=3,
        kind=kind,
        confidence=confidence,
        full_page_image_ratio=image_ratio,
        watermark_candidates=candidates,
    )


def candidate(confidence, evidence=("text_alias",), marker="tailieuonthi"):
    return WatermarkCandidate(
        kind="stream_or_text",
        confidence=confidence,
        marker=marker,
        page_indices=(0, 1, 2),
        evidence=evidence,
    )


def test_high_confidence_repeated_stream_routes_to_structural_fast_path():
    plan = build_processing_plan(
        profile(
            DocumentKind.VECTOR,
            candidate=candidate(0.95, ("text_alias", "repeated_stream")),
        )
    )
    assert plan.strategy is StrategyKind.STREAM_REMOVE
    assert plan.requires_strict_qc is False


def test_medium_confidence_vector_candidate_uses_conservative_vector_path():
    plan = build_processing_plan(profile(DocumentKind.VECTOR, candidate=candidate(0.70)))
    assert plan.strategy is StrategyKind.VECTOR_REMOVE
    assert plan.requires_strict_qc is True


def test_raster_document_routes_to_template_without_requiring_ocr_candidate():
    plan = build_processing_plan(profile(DocumentKind.RASTER, confidence=1.0, image_ratio=1.0))
    assert plan.strategy is StrategyKind.RASTER_TEMPLATE
    assert plan.requires_strict_qc is True


def test_low_confidence_candidate_falls_back_to_legacy():
    plan = build_processing_plan(profile(DocumentKind.VECTOR, candidate=candidate(0.6999)))
    assert plan.strategy is StrategyKind.LEGACY


def test_content_profile_is_a_safety_hint_not_an_engine_router():
    source = profile(
        DocumentKind.VECTOR,
        candidate=candidate(0.95, ("text_alias", "repeated_stream")),
    )
    auto = build_processing_plan(source, content_profile="auto")
    chemistry = build_processing_plan(source, content_profile="chemistry")
    assert auto.strategy is chemistry.strategy is StrategyKind.STREAM_REMOVE
    assert chemistry.content_profile == "chemistry"


@pytest.mark.parametrize("value", ["", "unknown", "math-engine"])
def test_content_profile_rejects_unknown_values(value):
    with pytest.raises(ValueError):
        build_processing_plan(profile(DocumentKind.RASTER, image_ratio=1.0), content_profile=value)
