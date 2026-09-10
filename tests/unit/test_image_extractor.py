from io import BytesIO

import fitz
from PIL import Image

from backend.engine.raster.image_extractor import find_full_page_image, extract_native_page_image


def _png_bytes(size=(640, 900)) -> bytes:
    image = Image.new("RGB", size, "white")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_find_full_page_image_returns_single_dominant_xobject(tmp_path):
    source = tmp_path / "raster.pdf"
    payload = _png_bytes()
    doc = fitz.open()
    page = doc.new_page(width=640, height=900)
    xref = page.insert_image(page.rect, stream=payload)
    doc.save(source)
    doc.close()

    doc = fitz.open(source)
    try:
        found = find_full_page_image(doc[0])
        assert found is not None
        assert found.xref == xref
        assert found.coverage >= 0.99
    finally:
        doc.close()


def test_extract_native_page_image_returns_xobject_bytes_and_dimensions(tmp_path, monkeypatch):
    source = tmp_path / "raster.pdf"
    payload = _png_bytes((640, 900))
    doc = fitz.open()
    page = doc.new_page(width=640, height=900)
    page.insert_image(page.rect, stream=payload)
    doc.save(source)
    doc.close()

    def fail_render(*args, **kwargs):
        raise AssertionError("page render must not be used by native fast path")

    monkeypatch.setattr(fitz.Page, "get_pixmap", fail_render)
    doc = fitz.open(source)
    try:
        native = extract_native_page_image(doc, 0)
        assert native is not None
        assert native.width == 640
        assert native.height == 900
        assert native.image_bytes
        assert native.extension == "png"
    finally:
        doc.close()


def test_find_full_page_image_rejects_page_with_multiple_competing_images(tmp_path):
    source = tmp_path / "multi.pdf"
    image = _png_bytes((300, 400))
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_image(fitz.Rect(0, 0, 300, 800), stream=image)
    page.insert_image(fitz.Rect(300, 0, 600, 800), stream=image)
    doc.save(source)
    doc.close()

    doc = fitz.open(source)
    try:
        assert find_full_page_image(doc[0]) is None
    finally:
        doc.close()
