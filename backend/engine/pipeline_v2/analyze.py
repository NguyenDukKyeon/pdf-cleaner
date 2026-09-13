from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.engine.analyzer.document_analyzer import analyze_document as _analyze_document
from backend.engine.analyzer.models import DocumentProfile
from backend.engine.pipeline_v2.analysis_cache import (
    AnalysisCache,
    fingerprint_pdf,
)

_ANALYSIS_CACHE = AnalysisCache(max_entries=32)


def get_analysis_cache() -> AnalysisCache:
    return _ANALYSIS_CACHE


def clear_analysis_cache() -> None:
    _ANALYSIS_CACHE.clear()


def analyze_document(
    path: str | Path, options: dict[str, Any] | None = None
) -> DocumentProfile:
    options = dict(options or {})
    kwargs: dict[str, Any] = {
        "max_samples": int(options.get("max_samples", 8) or 8),
    }
    if "stages" in options and options["stages"] is not None:
        kwargs["stages"] = tuple(int(s) for s in options["stages"])

    pdf_path = Path(path)
    fp = fingerprint_pdf(pdf_path)
    cached = _ANALYSIS_CACHE.get(pdf_path, fp)
    if cached is not None:
        return cached

    profile = _analyze_document(pdf_path, **kwargs)
    _ANALYSIS_CACHE.put(pdf_path, fp, profile)
    return profile


