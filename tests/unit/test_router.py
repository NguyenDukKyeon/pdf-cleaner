import pytest

from backend.engine.analyzer.models import DocumentKind, DocumentProfile, WatermarkCandidate
from backend.engine.router.models import EnginePreference, StrategyKind
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


def test_auto_smart_preference_returns_computed_strategy_and_sets_requested_engine():
    stream_profile = profile(
        DocumentKind.VECTOR,
        candidate=candidate(0.95, ("text_alias", "repeated_stream")),
    )
    plan = build_processing_plan(stream_profile, engine_preference="auto_smart")
    assert plan.strategy is StrategyKind.STREAM_REMOVE
    assert plan.requested_engine == "auto_smart"

    default_plan = build_processing_plan(stream_profile)
    assert default_plan.strategy is StrategyKind.STREAM_REMOVE
    assert default_plan.requested_engine == "auto_smart"


def test_stream_clean_preference_accepted_for_stream_document():
    stream_profile = profile(
        DocumentKind.VECTOR,
        candidate=candidate(0.95, ("text_alias", "repeated_stream")),
    )
    plan = build_processing_plan(stream_profile, engine_preference="stream_clean")
    assert plan.strategy is StrategyKind.STREAM_REMOVE
    assert plan.requested_engine == "stream_clean"

    enum_plan = build_processing_plan(
        stream_profile, engine_preference=EnginePreference.STREAM_CLEAN
    )
    assert enum_plan.strategy is StrategyKind.STREAM_REMOVE
    assert enum_plan.requested_engine == "stream_clean"


def test_stream_clean_preference_rejected_when_incompatible():
    raster_profile = profile(DocumentKind.RASTER, image_ratio=1.0)
    with pytest.raises(ValueError, match="Stream Clean"):
        build_processing_plan(raster_profile, engine_preference="stream_clean")

    vector_profile = profile(DocumentKind.VECTOR, candidate=candidate(0.70))
    with pytest.raises(ValueError, match="Stream Clean"):
        build_processing_plan(vector_profile, engine_preference="stream_clean")


def test_raster_clean_preference_accepted_for_raster_document():
    raster_profile = profile(DocumentKind.RASTER, image_ratio=1.0)
    plan = build_processing_plan(raster_profile, engine_preference="raster_clean")
    assert plan.strategy is StrategyKind.RASTER_TEMPLATE
    assert plan.requested_engine == "raster_clean"

    enum_plan = build_processing_plan(
        raster_profile, engine_preference=EnginePreference.RASTER_CLEAN
    )
    assert enum_plan.strategy is StrategyKind.RASTER_TEMPLATE
    assert enum_plan.requested_engine == "raster_clean"


def test_raster_clean_preference_rejected_when_incompatible():
    stream_profile = profile(
        DocumentKind.VECTOR,
        candidate=candidate(0.95, ("text_alias", "repeated_stream")),
    )
    with pytest.raises(ValueError, match="Raster Clean"):
        build_processing_plan(stream_profile, engine_preference="raster_clean")

    vector_profile = profile(DocumentKind.VECTOR, candidate=candidate(0.70))
    with pytest.raises(ValueError, match="Raster Clean"):
        build_processing_plan(vector_profile, engine_preference="raster_clean")


def test_compatibility_clean_replaces_strategy_with_legacy_and_clears_operations():
    stream_profile = profile(
        DocumentKind.VECTOR,
        candidate=candidate(0.95, ("text_alias", "repeated_stream")),
    )
    plan = build_processing_plan(stream_profile, engine_preference="compatibility_clean")
    assert plan.strategy is StrategyKind.LEGACY
    assert plan.operations == ()
    assert plan.requested_engine == "compatibility_clean"

    raster_profile = profile(DocumentKind.RASTER, image_ratio=1.0)
    raster_plan = build_processing_plan(raster_profile, engine_preference="compatibility_clean")
    assert raster_plan.strategy is StrategyKind.LEGACY
    assert raster_plan.operations == ()
    assert raster_plan.requested_engine == "compatibility_clean"


@pytest.mark.parametrize("invalid", ["", "unknown", "vector_repair", "vector_clean", "deep_clean"])
def test_engine_preference_rejects_invalid_values(invalid):
    stream_profile = profile(
        DocumentKind.VECTOR,
        candidate=candidate(0.95, ("text_alias", "repeated_stream")),
    )
    with pytest.raises(ValueError, match=f"unknown engine preference: {invalid}"):
        build_processing_plan(stream_profile, engine_preference=invalid)


def test_engine_preference_enum_values():
    assert EnginePreference.AUTO_SMART.value == "auto_smart"
    assert EnginePreference.STREAM_CLEAN.value == "stream_clean"
    assert EnginePreference.RASTER_CLEAN.value == "raster_clean"
    assert EnginePreference.COMPATIBILITY_CLEAN.value == "compatibility_clean"
    assert "vector_repair" not in [e.value for e in EnginePreference]
    assert "vector_remove" not in [e.value for e in EnginePreference]
