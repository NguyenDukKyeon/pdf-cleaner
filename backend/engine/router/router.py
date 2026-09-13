from dataclasses import replace

from backend.engine.analyzer.models import DocumentKind, DocumentProfile, WatermarkCandidate

from .models import EnginePreference, ProcessingOperation, ProcessingPlan, StrategyKind

_ALLOWED_CONTENT_PROFILES = {"auto", "math", "physics", "chemistry", "ebook"}


def _best_candidate(profile: DocumentProfile) -> WatermarkCandidate | None:
    if not profile.watermark_candidates:
        return None
    return max(profile.watermark_candidates, key=lambda item: item.confidence)


def build_processing_plan(
    profile: DocumentProfile,
    *,
    content_profile: str = "auto",
    engine_preference: str = "auto_smart",
) -> ProcessingPlan:
    if content_profile not in _ALLOWED_CONTENT_PROFILES:
        raise ValueError(f"unknown content profile: {content_profile}")

    try:
        preference = EnginePreference(engine_preference)
    except ValueError:
        raise ValueError(f"unknown engine preference: {engine_preference}")

    candidate = _best_candidate(profile)

    if profile.kind is DocumentKind.RASTER and profile.full_page_image_ratio >= 0.8:
        confidence = min(profile.confidence, candidate.confidence if candidate else 0.85)
        marker = candidate.marker if candidate else None
        auto_plan = ProcessingPlan(
            strategy=StrategyKind.RASTER_TEMPLATE,
            confidence=confidence,
            operations=(ProcessingOperation("learn_and_apply_raster_template", marker),),
            requires_strict_qc=confidence < 0.95,
            content_profile=content_profile,
            reason="raster document with dominant page imagery",
        )
    elif candidate is None or candidate.confidence < 0.70:
        confidence = candidate.confidence if candidate else min(profile.confidence, 0.69)
        auto_plan = ProcessingPlan(
            strategy=StrategyKind.LEGACY,
            confidence=confidence,
            content_profile=content_profile,
            reason="insufficient V2 watermark evidence",
        )
    elif candidate.confidence >= 0.95 and "repeated_stream" in candidate.evidence:
        auto_plan = ProcessingPlan(
            strategy=StrategyKind.STREAM_REMOVE,
            confidence=candidate.confidence,
            operations=(ProcessingOperation("remove_repeated_stream", candidate.marker),),
            requires_strict_qc=False,
            content_profile=content_profile,
            reason="high-confidence repeated structural watermark",
        )
    else:
        auto_plan = ProcessingPlan(
            strategy=StrategyKind.VECTOR_REMOVE,
            confidence=candidate.confidence,
            operations=(ProcessingOperation("remove_vector_candidate", candidate.marker),),
            requires_strict_qc=True,
            content_profile=content_profile,
            reason="vector watermark evidence requires conservative QC",
        )

    if preference is EnginePreference.AUTO_SMART:
        return auto_plan

    if preference is EnginePreference.STREAM_CLEAN:
        if auto_plan.strategy is not StrategyKind.STREAM_REMOVE:
            raise ValueError(
                f"Stream Clean preference is incompatible with document (detected strategy: {auto_plan.strategy.value})"
            )
        return replace(auto_plan, requested_engine=preference.value)

    if preference is EnginePreference.RASTER_CLEAN:
        if auto_plan.strategy is not StrategyKind.RASTER_TEMPLATE:
            raise ValueError(
                f"Raster Clean preference is incompatible with document (detected strategy: {auto_plan.strategy.value})"
            )
        return replace(auto_plan, requested_engine=preference.value)

    if preference is EnginePreference.COMPATIBILITY_CLEAN:
        return replace(
            auto_plan,
            strategy=StrategyKind.LEGACY,
            operations=(),
            requested_engine=preference.value,
        )

    raise ValueError(f"unknown engine preference: {engine_preference}")
