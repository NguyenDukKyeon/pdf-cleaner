from __future__ import annotations

import numpy as np

from backend.engine.raster.tdm_guided import (
    compute_watermark_residual_score,
    transfer_working_cleanup_to_native,
)


def test_transfer_working_cleanup_changes_only_trusted_watermark_pixels():
    native = np.full((12, 16, 3), 240, dtype=np.uint8)
    native[2:10, 1:3] = (20, 40, 60)  # protected content outside watermark zone
    working = native[::2, ::2].copy()
    cleaned = working.copy()
    cleaned[:, :] = np.clip(cleaned.astype(np.int16) + 30, 0, 255).astype(np.uint8)
    trusted = np.zeros(working.shape[:2], dtype=bool)
    trusted[2:5, 4:7] = True

    output, changed = transfer_working_cleanup_to_native(native, cleaned, trusted)

    expected_mask = np.zeros(native.shape[:2], dtype=bool)
    expected_mask[4:10, 8:14] = True
    assert changed > 0
    np.testing.assert_array_equal(output[~expected_mask], native[~expected_mask])
    assert np.any(output[expected_mask] != native[expected_mask])


def test_watermark_residual_score_distinguishes_visible_ghost_from_clean_background():
    original = np.full((40, 60, 3), 250, dtype=np.uint8)
    original[10:30, 20:40] = 185
    candidate = np.zeros((40, 60), dtype=bool)
    candidate[10:30, 20:40] = True
    background = np.full((40, 60), 250, dtype=np.uint8)

    unchanged_score = compute_watermark_residual_score(original, original, candidate, background)
    cleaned = original.copy()
    cleaned[candidate] = 248
    cleaned_score = compute_watermark_residual_score(original, cleaned, candidate, background)

    assert unchanged_score > 0.90
    assert cleaned_score < 0.08


def _make_linked_raster_pdf(path, uri):
    import fitz
    from io import BytesIO
    from PIL import Image

    image = Image.new("RGB", (600, 800), "white")
    buf = BytesIO(); image.save(buf, format="PNG")
    doc = fitz.open(); page = doc.new_page(width=600, height=800)
    page.insert_image(page.rect, stream=buf.getvalue())
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(10, 10, 200, 30), "uri": uri})
    doc.save(path); doc.close()


def test_safe_raster_validation_allows_only_known_watermark_links(tmp_path):
    from backend.engine.raster.tdm_guided import _validate_safe_raster_document

    allowed = tmp_path / "allowed.pdf"
    _make_linked_raster_pdf(allowed, "https://TaiLieuOnThi.Net")
    geometries, _ = _validate_safe_raster_document(allowed)
    assert geometries == [(600.0, 800.0)]

    unrelated = tmp_path / "unrelated.pdf"
    _make_linked_raster_pdf(unrelated, "https://example.com/important")
    import pytest
    with pytest.raises(RuntimeError, match="unrelated links"):
        _validate_safe_raster_document(unrelated)


def _make_two_page_raster_pdf(path):
    import fitz
    from io import BytesIO
    from PIL import Image

    doc = fitz.open()
    for _ in range(2):
        image = Image.new("RGB", (600, 800), "white")
        buf = BytesIO()
        image.save(buf, format="PNG")
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=buf.getvalue())
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(10, 10, 200, 30), "uri": "https://TaiLieuOnThi.Net"})
    doc.save(path)
    doc.close()


def test_tdm_guided_result_footer_fields_defaults():
    from backend.engine.raster.tdm_guided import TdmGuidedResult

    res = TdmGuidedResult(
        changed_pages=1,
        changed_pixels=10,
        native_image_pages=1,
        watermark_residual_score=0.02,
        outside_change_ratio=0.0,
        work_dpi=200,
    )
    assert res.footer_cleanup_level is None
    assert res.footer_residual_score is None
    assert res.footer_protected_change_ratio is None
    assert res.page_footer_residual_scores == ()


def test_clean_tailieuonthi_document_forwards_footer_cleanup_and_aggregates_metrics(tmp_path, monkeypatch):
    import json
    import backend.engine.raster.tdm_guided as tdm_module
    from backend.engine.raster.tdm_guided import clean_tailieuonthi_document
    from PIL import Image

    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    _make_two_page_raster_pdf(src)

    calls = []

    def fake_worker(input_pdf, page_index, output_png, result_json, *, work_dpi, footer_cleanup="auto", should_cancel=None):
        calls.append({
            "input_pdf": input_pdf,
            "page_index": page_index,
            "work_dpi": work_dpi,
            "footer_cleanup": footer_cleanup,
        })
        output_png.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 800), "white").save(output_png, format="PNG")
        if page_index == 0:
            metrics = {
                "changed_pixels": 10,
                "changed_pixel_ratio": 0.0001,
                "outside_change_ratio": 0.0,
                "watermark_residual_score": 0.02,
                "width": 600,
                "height": 800,
                "work_dpi": work_dpi,
                "footer_cleanup_level": "standard",
                "footer_residual_score": 0.012,
                "footer_protected_change_ratio": 0.0003,
            }
        else:
            metrics = {
                "changed_pixels": 25,
                "changed_pixel_ratio": 0.0002,
                "outside_change_ratio": 0.0,
                "watermark_residual_score": 0.03,
                "width": 600,
                "height": 800,
                "work_dpi": work_dpi,
                "footer_cleanup_level": "deep",
                "footer_residual_score": 0.028,
                "footer_protected_change_ratio": 0.0015,
            }
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(metrics), encoding="utf-8")
        return metrics

    monkeypatch.setattr(tdm_module, "_run_page_worker", fake_worker)

    result = clean_tailieuonthi_document(src, out, footer_cleanup="deep")

    assert len(calls) == 2
    assert calls[0]["footer_cleanup"] == "deep"
    assert calls[1]["footer_cleanup"] == "deep"
    assert result.footer_cleanup_level == "deep"
    assert result.footer_residual_score == 0.028
    assert result.footer_protected_change_ratio == 0.0015
    assert result.page_footer_residual_scores == (0.012, 0.028)


def test_clean_tailieuonthi_document_footer_cleanup_standard_when_no_escalation(tmp_path, monkeypatch):
    import json
    import backend.engine.raster.tdm_guided as tdm_module
    from backend.engine.raster.tdm_guided import clean_tailieuonthi_document
    from PIL import Image

    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    _make_two_page_raster_pdf(src)

    def fake_worker(input_pdf, page_index, output_png, result_json, *, work_dpi, footer_cleanup="auto", should_cancel=None):
        output_png.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 800), "white").save(output_png, format="PNG")
        metrics = {
            "changed_pixels": 10,
            "changed_pixel_ratio": 0.0001,
            "outside_change_ratio": 0.0,
            "watermark_residual_score": 0.02,
            "width": 600,
            "height": 800,
            "work_dpi": work_dpi,
            "footer_cleanup_level": "standard",
            "footer_residual_score": 0.010,
            "footer_protected_change_ratio": 0.0001,
        }
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(metrics), encoding="utf-8")
        return metrics

    monkeypatch.setattr(tdm_module, "_run_page_worker", fake_worker)

    result = clean_tailieuonthi_document(src, out, footer_cleanup="auto")
    assert result.footer_cleanup_level == "standard"
    assert result.footer_residual_score == 0.010
    assert result.footer_protected_change_ratio == 0.0001
    assert result.page_footer_residual_scores == (0.010, 0.010)


def test_clean_native_page_calls_footer_polish_and_resizes_protect_mask(tmp_path, monkeypatch):
    import backend.engine.raster.tdm_guided as tdm_module
    from backend.engine.raster.footer_polish import FooterPolishMetrics
    from PIL import Image
    import numpy as np

    src = tmp_path / "src.pdf"
    out_png = tmp_path / "page-0.png"
    _make_linked_raster_pdf(src, "https://TaiLieuOnThi.Net")

    class FakeTdmModule:
        def create_settings_from_config(self, cfg):
            return object()
        def _namespace_from_settings(self, s):
            import argparse
            return argparse.Namespace()
        def clean_page_bgr(self, bgr, args):
            h, w = bgr.shape[:2]
            safe_protect = np.zeros((h, w), dtype=np.uint8)
            safe_protect[10:20, 10:20] = 255
            debug = {
                "header_footer_mask": np.zeros((h, w), dtype=np.uint8),
                "diag_band": np.zeros((h, w), dtype=np.uint8),
                "diag_replace": np.zeros((h, w), dtype=np.uint8),
                "safe_protect": safe_protect,
                "local_bg_gray": np.full((h, w), 255, dtype=np.uint8),
            }
            return bgr, debug

    monkeypatch.setattr(tdm_module, "_load_tdm_module", lambda: FakeTdmModule())

    polish_calls = []
    def fake_polish(native_rgb, *, config, content_protect_mask=None):
        polish_calls.append({
            "native_rgb_shape": native_rgb.shape,
            "level": config.level,
            "protect_mask_shape": None if content_protect_mask is None else content_protect_mask.shape,
            "protect_mask_active": None if content_protect_mask is None else bool(np.any(content_protect_mask)),
        })
        return native_rgb.copy(), FooterPolishMetrics(
            level_used=config.level if config.level != "auto" else "standard",
            changed_pixels=5,
            residual_score_before=0.04,
            residual_score_after=0.015,
            protected_change_ratio=0.0002,
        )

    monkeypatch.setattr(tdm_module, "polish_footer_residual", fake_polish)

    metrics = tdm_module._clean_native_page(src, 0, out_png, work_dpi=150, footer_cleanup="deep")

    assert len(polish_calls) == 1
    assert polish_calls[0]["native_rgb_shape"] == (800, 600, 3)
    assert polish_calls[0]["level"] == "deep"
    assert polish_calls[0]["protect_mask_shape"] == (800, 600)
    assert polish_calls[0]["protect_mask_active"] is True
    assert metrics["footer_cleanup_level"] == "deep"
    assert metrics["footer_residual_score"] == 0.015
    assert metrics["footer_protected_change_ratio"] == 0.0002

