from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.engine.analyzer.document_analyzer import analyze_document as _analyze_document
from backend.engine.analyzer.models import DocumentProfile
from backend.engine.pipeline_v2.analysis_cache import (
    AnalysisCache,
    fingerprint_pdf,
    freeze_analysis_options,
)

_ANALYSIS_CACHE = AnalysisCache(max_entries=32)


def get_analysis_cache() -> AnalysisCache:
    return _ANALYSIS_CACHE


def clear_analysis_cache() -> None:
    _ANALYSIS_CACHE.clear()


def analyze_document(
    path: str | Path, options: dict[str, Any] | None = None
) -> DocumentProfile:
    raw_options = dict(options or {})
    kwargs: dict[str, Any] = {
        "max_samples": int(raw_options.get("max_samples", 8) or 8),
    }
    if "stages" in raw_options and raw_options["stages"] is not None:
        kwargs["stages"] = tuple(int(s) for s in raw_options["stages"])

    norm_options = freeze_analysis_options(raw_options)

    pdf_path = Path(path)
    fp = fingerprint_pdf(pdf_path)
    cached = _ANALYSIS_CACHE.get(pdf_path, fp, options=norm_options)
    if cached is not None:
        return cached

    profile = _analyze_document(pdf_path, **kwargs)
    _ANALYSIS_CACHE.put(pdf_path, fp, profile, options=norm_options)
    return profile



