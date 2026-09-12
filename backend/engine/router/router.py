from __future__ import annotations

from backend.engine.analyzer.models import DocumentKind, DocumentProfile, WatermarkCandidate

from .models import ProcessingOperation, ProcessingPlan, StrategyKind

_ALLOWED_CONTENT_PROFILES = {"auto", "math", "physics", "chemistry", "ebook"}


def _best_candidate(profile: DocumentProfile) -> WatermarkCandidate | None:
    if not profile.watermark_candidates:
        return None
    return max(profile.watermark_candidates, key=lambda item: item.confidence)


def build_processing_plan(
    profile: DocumentProfile,
    *,
    content_profile: str = "auto",
) -> ProcessingPlan:
    if content_profile not in _ALLOWED_CONTENT_PROFILES:
        raise ValueError(f"unknown content profile: {content_profile}")

    candidate = _best_candidate(profile)

    if profile.kind is DocumentKind.RASTER and profile.full_page_image_ratio >= 0.8:
        confidence = min(profile.confidence, candidate.confidence if candidate else 0.85)
        marker = candidate.marker if candidate else None
        return ProcessingPlan(
            strategy=StrategyKind.RASTER_TEMPLATE,
            confidence=confidence,
            operations=(ProcessingOperation("learn_and_apply_raster_template", marker),),
            requires_strict_qc=confidence < 0.95,
            content_profile=content_profile,
            reason="raster document with dominant page imagery",
        )

    if candidate is None or candidate.confidence < 0.70:
        confidence = candidate.confidence if candidate else min(profile.confidence, 0.69)
        return ProcessingPlan(
            strategy=StrategyKind.LEGACY,
            confidence=confidence,
            content_profile=content_profile,
            reason="insufficient V2 watermark evidence",
        )

    if candidate.confidence >= 0.95 and "repeated_stream" in candidate.evidence:
        return ProcessingPlan(
            strategy=StrategyKind.STREAM_REMOVE,
            confidence=candidate.confidence,
            operations=(ProcessingOperation("remove_repeated_stream", candidate.marker),),
            requires_strict_qc=False,
            content_profile=content_profile,
            reason="high-confidence repeated structural watermark",
        )

    return ProcessingPlan(
        strategy=StrategyKind.VECTOR_REMOVE,
        confidence=candidate.confidence,
        operations=(ProcessingOperation("remove_vector_candidate", candidate.marker),),
        requires_strict_qc=True,
        content_profile=content_profile,
        reason="vector watermark evidence requires conservative QC",
    )
