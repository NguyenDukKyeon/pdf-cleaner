from __future__ import annotations
from pathlib import Path
from typing import Any
from backend.engine.analyzer.document_analyzer import analyze_document as _analyze_document

def analyze_document(path: str | Path, options: dict[str, Any] | None = None):
    options = dict(options or {})
    kwargs: dict[str, Any] = {
        "max_samples": int(options.get("max_samples", 8) or 8),
    }
    if "stages" in options and options["stages"] is not None:
        kwargs["stages"] = tuple(int(s) for s in options["stages"])
    return _analyze_document(path, **kwargs)

