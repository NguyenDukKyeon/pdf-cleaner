from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import fitz

from .models import DocumentKind, DocumentProfile, PageEvidence, WatermarkCandidate
from .raster_analyzer import full_page_image_coverage, page_image_hashes
from .stream_analyzer import page_stream_hashes, repeated_hashes
from ..signatures.registry import SignatureRegistry


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


def _classify_document(text_layer_ratio: float, full_page_image_ratio: float) -> tuple[DocumentKind, float]:
    if full_page_image_ratio >= 0.8 and text_layer_ratio <= 0.2:
        return DocumentKind.RASTER, max(full_page_image_ratio, 1.0 - text_layer_ratio)
    if text_layer_ratio >= 0.8 and full_page_image_ratio < 0.15:
        return DocumentKind.VECTOR, max(text_layer_ratio, 1.0 - full_page_image_ratio)
    return DocumentKind.HYBRID, 0.8


def analyze_document(
    path: str | Path,
    *,
    max_samples: int = 8,
    signature_registry: SignatureRegistry | None = None,
) -> DocumentProfile:
    registry = signature_registry or SignatureRegistry.load_default()
    doc = fitz.open(str(path))
    try:
        if doc.page_count <= 0:
            raise ValueError("PDF must contain at least one page")
        sampled_pages = select_sample_pages(doc.page_count, max_samples=max_samples)
        evidence = []
        text_by_page: dict[int, str] = {}

        for page_index in sampled_pages:
            page = doc[page_index]
            text = page.get_text("text").strip()
            coverage = full_page_image_coverage(page)
            text_by_page[page_index] = text
            evidence.append(
                PageEvidence(
                    page_index=page_index,
                    text_coverage=1.0 if text else 0.0,
                    full_page_image_coverage=coverage,
                    text_char_count=len(text),
                    stream_hashes=page_stream_hashes(doc, page),
                    image_hashes=page_image_hashes(doc, page),
                )
            )

        sample_count = len(evidence)
        text_layer_ratio = sum(item.text_char_count > 0 for item in evidence) / sample_count
        full_page_image_ratio = sum(item.full_page_image_coverage for item in evidence) / sample_count
        kind, confidence = _classify_document(text_layer_ratio, full_page_image_ratio)

        repeated_streams = repeated_hashes(tuple(item.stream_hashes for item in evidence))
        signature_pages: dict[str, set[int]] = defaultdict(set)
        for page_index, text in text_by_page.items():
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

        return DocumentProfile(
            page_count=doc.page_count,
            kind=kind,
            confidence=confidence,
            sampled_pages=sampled_pages,
            page_evidence=tuple(evidence),
            watermark_candidates=tuple(candidates),
            text_layer_ratio=text_layer_ratio,
            full_page_image_ratio=full_page_image_ratio,
        )
    finally:
        doc.close()
