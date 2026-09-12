from __future__ import annotations
from backend.engine.router.router import build_processing_plan

def plan_document(profile, options: dict | None = None):
    options = dict(options or {})
    return build_processing_plan(profile, content_profile=str(options.get('content_profile') or 'auto'))
