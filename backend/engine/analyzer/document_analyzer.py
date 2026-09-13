from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import fitz

from .models import DocumentKind, DocumentProfile, PageEvidence, WatermarkCandidate
from .raster_analyzer import full_page_image_coverage, page_image_hashes
from .stream_analyzer import page_stream_hashes, repeated_hashes
from ..signatures.registry import SignatureRegistry
from ..raster.tdm_guided import probe_tailieuonthi_signature


def select_sample_pages(page_count: int, max_samples: int = 8) -> tuple[int, ...]:
    """Return deterministic, evenly distributed zero-based page indices."""
    if page_count <= 0:
        raise ValueError("page_count must be positive")
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")

    sample_count = min(page_count, max_samples)
    if sample_count == page_count:
        return tuple(range(page_count))
    if sample_count == 1:
        return (0,)

    last_page = page_count - 1
    denominator = sample_count - 1
    return tuple(round(index * last_page / denominator) for index in range(sample_count))


def select_staged_sample_pages(
    page_count: int,
    stages: tuple[int, ...] = (3, 5, 8),
) -> tuple[tuple[int, ...], ...]:
    """Return nested tuples of page indices for staged document sampling.

    Each stage is a superset of the preceding stage. Capped at page_count,
    and redundant stages are excluded.
    """
    if page_count <= 0:
        raise ValueError("page_count must be positive")
    if not stages or any(s <= 0 for s in stages):
        raise ValueError("stages must contain positive integers")

    unique_sorted_stages = tuple(sorted(set(stages)))
    target_sizes: list[int] = []
    for s in unique_sorted_stages:
        sz = min(page_count, s)
        if not target_sizes or sz > target_sizes[-1]:
            target_sizes.append(sz)

    if not target_sizes:
        return ()

    current = select_sample_pages(page_count, max_samples=target_sizes[-1])
    result = [current]
    for sz in reversed(target_sizes[:-1]):
        sub_indices = select_sample_pages(len(current), max_samples=sz)
        current = tuple(current[i] for i in sub_indices)
        result.append(current)

    return tuple(reversed(result))


def _classify_document(text_layer_ratio: float, full_page_image_ratio: float) -> tuple[DocumentKind, float]:
    if full_page_image_ratio >= 0.8 and text_layer_ratio <= 0.2:
        return DocumentKind.RASTER, max(full_page_image_ratio, 1.0 - text_layer_ratio)
    if text_layer_ratio >= 0.8 and full_page_image_ratio < 0.15:
        return DocumentKind.VECTOR, max(text_layer_ratio, 1.0 - full_page_image_ratio)
    return DocumentKind.HYBRID, 0.8


def _collect_page_evidence(doc: fitz.Document, page_index: int) -> tuple[PageEvidence, str]:
    page = doc[page_index]
    text = page.get_text("text").strip()
    coverage = full_page_image_coverage(page)
    evidence = PageEvidence(
        page_index=page_index,
        text_coverage=1.0 if text else 0.0,
        full_page_image_coverage=coverage,
        text_char_count=len(text),
        stream_hashes=page_stream_hashes(doc, page),
        image_hashes=page_image_hashes(doc, page),
    )
    return evidence, text


def _profile_from_evidence(
    doc: fitz.Document,
    sampled_pages: tuple[int, ...],
    evidence: tuple[PageEvidence, ...],
    text_by_page: dict[int, str],
    registry: SignatureRegistry,
    probe_cache: dict[tuple[int, ...], tuple[float, tuple[int, ...]]] | None = None,
) -> DocumentProfile:
    sample_count = len(evidence)
    text_layer_ratio = sum(item.text_char_count > 0 for item in evidence) / sample_count
    full_page_image_ratio = sum(item.full_page_image_coverage for item in evidence) / sample_count
    kind, confidence = _classify_document(text_layer_ratio, full_page_image_ratio)

    repeated_streams = repeated_hashes(tuple(item.stream_hashes for item in evidence))
    signature_pages: dict[str, set[int]] = defaultdict(set)
    for page_index in sampled_pages:
        text = text_by_page.get(page_index, "")
        for signature in registry.match_text(text):
            signature_pages[signature.id].add(page_index)

    candidates = []
    for signature_id, pages in sorted(signature_pages.items()):
        page_set = set(pages)
        repeated_on_pages = any(
            repeated_streams.intersection(item.stream_hashes)
            for item in evidence
            if item.page_index in page_set
        )
        repetition_ratio = len(page_set) / sample_count
        candidate_confidence = min(
            1.0,
            0.45 + 0.35 * repetition_ratio + (0.20 if repeated_on_pages else 0.0),
        )
        candidates.append(
            WatermarkCandidate(
                kind="stream_or_text",
                confidence=candidate_confidence,
                marker=signature_id,
                page_indices=tuple(sorted(page_set)),
                evidence=("text_alias",) + (("repeated_stream",) if repeated_on_pages else ()),
            )
        )

    if kind is DocumentKind.RASTER and full_page_image_ratio >= 0.8:
        if not any(item.marker == "tailieuonthi" for item in candidates):
            probe_key = (sampled_pages[0],) if len(sampled_pages) == 1 else (sampled_pages[0], sampled_pages[-1])
            if probe_cache is not None and probe_key in probe_cache:
                probe_confidence, probe_pages = probe_cache[probe_key]
            else:
                try:
                    probe_confidence, probe_pages = probe_tailieuonthi_signature(doc, sampled_pages)
                except Exception:
                    probe_confidence, probe_pages = 0.0, ()
                if probe_cache is not None:
                    probe_cache[probe_key] = (probe_confidence, probe_pages)
            if probe_confidence >= 0.70 and probe_pages:
                candidates.append(
                    WatermarkCandidate(
                        kind="raster_signature",
                        confidence=probe_confidence,
                        marker="tailieuonthi",
                        page_indices=tuple(probe_pages),
                        evidence=("tdm_signature_probe", "header_footer", "diagonal"),
                    )
                )

    return DocumentProfile(
        page_count=doc.page_count,
        kind=kind,
        confidence=confidence,
        sampled_pages=sampled_pages,
        page_evidence=evidence,
        watermark_candidates=tuple(candidates),
        text_layer_ratio=text_layer_ratio,
        full_page_image_ratio=full_page_image_ratio,
    )


def analyze_document(
    path: str | Path,
    *,
    max_samples: int = 8,
    signature_registry: SignatureRegistry | None = None,
    stages: tuple[int, ...] | None = None,
) -> DocumentProfile:
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")

    registry = signature_registry or SignatureRegistry.load_default()
    doc = fitz.open(str(path))
    try:
        if doc.page_count <= 0:
            raise ValueError("PDF must contain at least one page")

        if stages is not None:
            effective_stages = stages
        elif max_samples == 8:
            effective_stages = (3, 5, 8)
        else:
            base = [s for s in (3, 5, 8) if s < max_samples]
            base.append(max_samples)
            effective_stages = tuple(base)

        staged_page_groups = select_staged_sample_pages(doc.page_count, stages=effective_stages)

        evidence_by_page: dict[int, PageEvidence] = {}
        text_by_page: dict[int, str] = {}
        probe_cache: dict[tuple[int, ...], tuple[float, tuple[int, ...]]] = {}
        profile: DocumentProfile | None = None

        for stage_idx, stage_pages in enumerate(staged_page_groups):
            for page_index in stage_pages:
                if page_index not in evidence_by_page:
                    ev, txt = _collect_page_evidence(doc, page_index)
                    evidence_by_page[page_index] = ev
                    text_by_page[page_index] = txt

            stage_evidence = tuple(evidence_by_page[p] for p in stage_pages)
            profile = _profile_from_evidence(
                doc,
                stage_pages,
                stage_evidence,
                text_by_page,
                registry,
                probe_cache=probe_cache,
            )

            # If this is the last available stage, stop and return
            if stage_idx == len(staged_page_groups) - 1:
                return profile

            best = max(profile.watermark_candidates, key=lambda c: c.confidence, default=None)
            if len(stage_pages) <= 3:
                if profile.confidence >= 0.95 and best is not None and best.confidence >= 0.95:
                    return profile
            elif len(stage_pages) <= 5:
                if profile.confidence >= 0.90 and best is not None and best.confidence >= 0.90:
                    return profile

        return profile  # type: ignore[return-value]
    finally:
        doc.close()

