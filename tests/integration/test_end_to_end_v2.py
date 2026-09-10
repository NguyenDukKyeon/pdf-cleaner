from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

from backend.app.processing_service import process_document_v2
from backend.engine.analyzer.models import DocumentKind
from backend.engine.router.models import StrategyKind


WATERMARK = "TAILIEUONTHI.NET"


def _png_page(seed: int) -> bytes:
    image = np.full((180, 240, 3), 252, dtype=np.uint8)
    y = 20 + seed * 22
    image[y : y + 3, 20:180] = 20
    for x in range(110, 225):
        yy = int(165 - 0.55 * (x - 110))
        image[max(0, yy - 2) : min(180, yy + 3), x : x + 2] = 205
    bio = BytesIO()
    Image.fromarray(image).save(bio, format="PNG")
    return bio.getvalue()


def _insert_watermark(page: fitz.Page) -> None:
    page.insert_text(
        (50, 165),
        WATERMARK,
        fontsize=12,
        color=(0.75, 0.75, 0.75),
    )


def _make_vector_pdf(path: Path) -> None:
    doc = fitz.open()
    for index in range(4):
        page = doc.new_page(width=240, height=180)
        page.insert_text((20, 30), f"Question {index + 1}: source content ABCD", fontsize=10)
        _insert_watermark(page)
    doc.save(path)
    doc.close()


def _make_hybrid_pdf(path: Path) -> None:
    doc = fitz.open()
    for index in range(4):
        page = doc.new_page(width=240, height=180)
        page.insert_image(page.rect, stream=_png_page(index))
        _insert_watermark(page)
    doc.save(path)
    doc.close()


def _make_raster_pdf(path: Path) -> None:
    doc = fitz.open()
    for index in range(5):
        page = doc.new_page(width=240, height=180)
        page.insert_image(page.rect, stream=_png_page(index))
    doc.save(path)
    doc.close()


def _all_text(path: Path) -> str:
    with fitz.open(path) as doc:
        return "\n".join(page.get_text("text") for page in doc)


def _first_image_rgb(path: Path) -> np.ndarray:
    with fitz.open(path) as doc:
        xref = doc[0].get_images(full=True)[0][0]
        payload = doc.extract_image(xref)["image"]
    return np.array(Image.open(BytesIO(payload)).convert("RGB"))


def test_vector_e2e_routes_to_stream_remove_and_qc_ignores_target_region(tmp_path: Path) -> None:
    source = tmp_path / "vector.pdf"
    output = tmp_path / "vector-clean.pdf"
    _make_vector_pdf(source)

    result = process_document_v2(
        source,
        output,
        options={
            "content_profile": "auto",
            "allow_legacy_fallback": False,
            "qc_dpi": 72,
            "max_outside_change_ratio": 0.001,
        },
    )

    assert result.profile.kind is DocumentKind.VECTOR
    assert result.plan.strategy is StrategyKind.STREAM_REMOVE
    assert result.report.strategy == StrategyKind.STREAM_REMOVE.value
    assert result.report.used_fallback is False
    assert result.report.rasterized_pages == 0
    assert result.qc.ok is True
    assert result.qc.outside_change_ratio <= 0.001
    assert result.report.metadata.get("watermark_regions")
    text = _all_text(output)
    assert WATERMARK not in text
    assert "Question 1: source content ABCD" in text


def test_hybrid_e2e_routes_to_stream_remove_and_preserves_native_page_image(tmp_path: Path) -> None:
    source = tmp_path / "hybrid.pdf"
    output = tmp_path / "hybrid-clean.pdf"
    _make_hybrid_pdf(source)
    before = _first_image_rgb(source)

    result = process_document_v2(
        source,
        output,
        options={
            "content_profile": "auto",
            "allow_legacy_fallback": False,
            "qc_dpi": 72,
            "max_outside_change_ratio": 0.001,
        },
    )

    assert result.profile.kind is DocumentKind.HYBRID
    assert result.plan.strategy is StrategyKind.STREAM_REMOVE
    assert result.report.strategy == StrategyKind.STREAM_REMOVE.value
    assert result.report.used_fallback is False
    assert result.report.rasterized_pages == 0
    assert result.qc.ok is True
    assert result.qc.outside_change_ratio <= 0.001
    assert WATERMARK not in _all_text(output)
    np.testing.assert_array_equal(_first_image_rgb(output), before)


def test_raster_e2e_uses_native_template_path_without_ocr(tmp_path: Path) -> None:
    source = tmp_path / "raster.pdf"
    output = tmp_path / "raster-clean.pdf"
    _make_raster_pdf(source)

    result = process_document_v2(
        source,
        output,
        options={
            "content_profile": "auto",
            "allow_legacy_fallback": False,
            "dpi": 120,
            "qc_dpi": 72,
        },
    )

    assert result.profile.kind is DocumentKind.RASTER
    assert result.plan.strategy is StrategyKind.RASTER_TEMPLATE
    assert result.report.strategy == StrategyKind.RASTER_TEMPLATE.value
    assert result.report.used_fallback is False
    assert result.report.rasterized_pages == 0
    assert result.report.native_image_pages == 5
    assert result.report.ocr_calls == 0
    assert result.qc.ok is True
    with fitz.open(source) as src, fitz.open(output) as out:
        assert src.page_count == out.page_count == 5
        assert [tuple(page.rect) for page in src] == [tuple(page.rect) for page in out]
