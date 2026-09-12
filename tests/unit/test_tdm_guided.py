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
