from __future__ import annotations
from pathlib import Path
from backend.engine.analyzer.document_analyzer import analyze_document as _analyze_document

def analyze_document(path: str | Path, options: dict | None = None):
    options = dict(options or {})
    return _analyze_document(path, max_samples=int(options.get('max_samples', 8) or 8))
