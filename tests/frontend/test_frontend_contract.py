from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "static" / "styles.css").read_text(encoding="utf-8")


def test_primary_workflow_is_auto_first_and_subjects_are_advanced_hints() -> None:
    assert 'id="modePicker"' not in HTML
    assert "let selectedMode = 'auto';" in JS
    assert "let selectedContentProfile = 'auto';" in JS
    assert 'id="contentProfile"' in HTML
    for value in ("auto", "math", "physics", "chemistry", "ebook"):
        assert f'value="{value}"' in HTML
    assert "content_profile: selectedContentProfile" in JS


def test_v2_stage_progress_and_processing_diagnostics_are_rendered() -> None:
    assert "event.type === 'stage'" in JS
    for stage in ("ANALYZING", "PLANNING", "PROCESSING", "VERIFYING"):
        assert stage in JS

    for field in (
        "strategy",
        "confidence",
        "worker_count",
        "native_image_pages",
        "ocr_calls",
    ):
        assert field in JS

    for element_id in (
        "stageList",
        "analysisRepresentation",
        "analysisStrategy",
        "analysisConfidence",
        "analysisWorkers",
        "analysisNativeImage",
        "analysisOcrCalls",
    ):
        assert f'id="{element_id}"' in HTML

    assert ".stage-list" in CSS
    assert ".diagnostics-grid" in CSS
