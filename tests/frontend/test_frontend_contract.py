from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "static" / "styles.css").read_text(encoding="utf-8")


def test_primary_workflow_is_auto_first_and_subjects_are_advanced_hints() -> None:
    assert 'id="modePicker"' not in HTML
    assert "let selectedMode = 'auto';" in JS
    assert "let selectedEnginePreference = 'auto_smart';" in JS
    assert "let selectedContentProfile = 'auto';" in JS
    assert "let selectedFooterCleanup = 'auto';" in JS
    assert 'id="contentProfilePicker"' in HTML
    for value in ("auto", "math", "physics", "chemistry", "ebook"):
        assert f'data-content-profile="{value}"' in HTML
    assert "engine_preference: selectedEnginePreference" in JS
    assert "content_profile: selectedContentProfile" in JS
    assert "footer_cleanup: selectedFooterCleanup" in JS


def test_engine_picker_cards_and_contracts() -> None:
    import re

    assert 'id="enginePicker"' in HTML
    engine_cards = {
        "auto_smart": ("Auto Smart", "Tự chọn cách xử lý nhanh và an toàn nhất cho từng PDF."),
        "stream_clean": ("Stream Clean", "Nhanh nhất — giữ nguyên chữ, vector và chất lượng PDF gốc."),
        "raster_clean": ("Raster Clean", "Dành cho PDF scan/ảnh — ưu tiên giữ chi tiết ảnh gốc."),
        "compatibility_clean": ("Compatibility Clean", "Cho PDF khó hoặc chưa đủ chắc chắn để dùng engine nhanh."),
    }
    for engine, (title, copy) in engine_cards.items():
        assert f'data-engine="{engine}"' in HTML
        assert title in HTML
        assert copy in HTML

    # Assert no selectable [data-engine] says Vector Repair or has vector_remove value
    data_engines = re.findall(r'data-engine=["\']([^"\']+)["\']', HTML)
    assert set(data_engines) == set(engine_cards.keys())
    for card_match in re.finditer(r'<button[^>]*data-engine=["\']([^"\']+)["\'][^>]*>(.*?)</button>', HTML, re.DOTALL):
        engine_val = card_match.group(1)
        card_content = card_match.group(2)
        assert "Vector Repair" not in card_content, f"data-engine={engine_val} must not say Vector Repair"
        assert engine_val != "vector_remove"

    # Strategy labels in app.js
    assert "stream_remove: 'Stream Clean'" in JS
    assert "vector_remove: 'Vector Repair'" in JS
    assert "raster_template: 'Raster Clean'" in JS
    assert "legacy: 'Compatibility Clean'" in JS


def test_content_protection_cards_and_contracts() -> None:
    import re

    assert 'id="contentProfilePicker"' in HTML
    protection_cards = {
        "auto": ("Auto", "Tự cân bằng làm sạch và bảo toàn nội dung."),
        "math": ("Formula & Diagram Safe", "Giữ công thức, bảng, đồ thị và đường kẻ rõ nét."),
        "physics": ("Diagram & Line Safe", "Ưu tiên sơ đồ, hình minh họa và các nét mảnh."),
        "chemistry": ("Symbol & Structure Safe", "Bảo vệ ký hiệu, chỉ số nhỏ và cấu trúc công thức."),
        "ebook": ("Text & Image Safe", "Giữ chữ dài, ảnh, bìa màu và bố cục ebook."),
    }
    for profile, (title, copy) in protection_cards.items():
        assert f'data-content-profile="{profile}"' in HTML
        assert title in HTML
        assert copy in HTML

    data_profiles = re.findall(r'data-content-profile=["\']([^"\']+)["\']', HTML)
    assert set(data_profiles) == set(protection_cards.keys())


def test_footer_cleanup_control_and_contracts() -> None:
    import re

    assert 'id="footerCleanup"' in HTML
    footer_cards = {
        "auto": ("Auto", "Tự tăng mức làm sạch nếu chân trang vẫn còn vệt mờ."),
        "standard": ("Standard", "Làm sạch nhẹ vùng URL ở chân trang."),
        "deep": ("Deep", "Làm sạch kỹ vệt mờ còn sót, vẫn giữ số trang và nội dung thật."),
    }
    for level, (title, copy) in footer_cards.items():
        assert f'data-footer-cleanup="{level}"' in HTML
        assert title in HTML
        assert copy in HTML

    data_levels = re.findall(r'data-footer-cleanup=["\']([^"\']+)["\']', HTML)
    assert set(data_levels) == set(footer_cards.keys())


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
        "footer_cleanup_level",
        "footer_residual_score",
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
        "analysisFooterCleanup",
        "analysisFooterResidual",
    ):
        assert f'id="{element_id}"' in HTML

    assert ".stage-list" in CSS
    assert ".diagnostics-grid" in CSS
    assert ".engine-card" in CSS or ".engine-picker" in CSS
    assert ".protection-card" in CSS or ".protection-picker-grid" in CSS
    assert ".footer-cleanup-card" in CSS or ".footer-cleanup-picker" in CSS


def test_footer_diagnostics_rendering_contract() -> None:
    assert 'id="analysisFooterCleanup"' in HTML
    assert 'id="analysisFooterResidual"' in HTML
    assert "#analysisFooterCleanup" in JS
    assert "#analysisFooterResidual" in JS
    assert "footer_cleanup_level" in JS
    assert "footer_residual_score" in JS


def test_preset_picker_and_accessibility_contract() -> None:
    assert 'id="presetPicker"' in HTML
    for preset in ("fast", "balanced", "high_quality", "safe_mode"):
        assert f'data-preset="{preset}"' in HTML

    # Verify selectPreset updates aria-checked for accessibility
    assert "function selectPreset(preset, applyValues = true)" in JS
    assert "card.setAttribute('aria-checked', card.dataset.preset === selectedPreset ? 'true' : 'false')" in JS


def test_preset_quality_invariants_and_footer_cleanup_defaults() -> None:
    import re

    # Default selected preset remains 'balanced'
    assert "let selectedPreset = 'balanced';" in JS

    # Footer cleanup default remains 'auto'
    assert "let selectedFooterCleanup = 'auto';" in JS

    # Extract presetValues object definition from JS
    match = re.search(r"let presetValues = ({.*?});", JS, re.DOTALL)
    assert match is not None, "presetValues definition must exist in app.js"
    preset_block = match.group(1)

    # Invariants for each preset:
    # Fast: dpi 200, output_dpi 200, quality 88
    # Balanced: dpi 240, output_dpi 240, quality 92
    # HighQuality: dpi 320, output_dpi 320, quality 95
    # Safe: dpi 240, output_dpi 240, quality 95, worker request 1 (cpu: 1)
    expected_invariants = {
        "fast": {"dpi": 200, "output_dpi": 200, "quality": 88, "cpu": 0},
        "balanced": {"dpi": 240, "output_dpi": 240, "quality": 92, "cpu": 0},
        "high_quality": {"dpi": 320, "output_dpi": 320, "quality": 95, "cpu": 0},
        "safe_mode": {"dpi": 240, "output_dpi": 240, "quality": 95, "cpu": 1},
    }
    for preset_name, expected in expected_invariants.items():
        assert f"{preset_name}:" in preset_block
        for k, v in expected.items():
            assert re.search(rf"{preset_name}:\s*\{{[^}}]*\b{k}:\s*{v}\b", preset_block) is not None, (
                f"Preset {preset_name} must have {k}={v}"
            )

    # selectPreset function must never disable or mutate footer cleanup
    select_preset_match = re.search(r"function selectPreset\([^)]*\)\s*\{(.*?)\n\}", JS, re.DOTALL)
    assert select_preset_match is not None
    assert "selectedFooterCleanup" not in select_preset_match.group(1)
    assert "footer_cleanup" not in select_preset_match.group(1)

    # commonPayload preserves both preset and footer_cleanup defaults
    assert "footer_cleanup: selectedFooterCleanup" in JS
    assert "preset: selectedPreset" in JS



