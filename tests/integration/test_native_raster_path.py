import fitz

from backend.engine.raster.image_extractor import extract_native_page_image
from tests.fixtures_factory import make_raster_pdf


def test_native_raster_path_extracts_each_full_page_image_without_rasterizing(tmp_path, monkeypatch):
    source = make_raster_pdf(tmp_path / "raster.pdf", pages=3)

    def fail_render(*args, **kwargs):
        raise AssertionError("native raster extraction must not render pages")

    monkeypatch.setattr(fitz.Page, "get_pixmap", fail_render)
    doc = fitz.open(source)
    try:
        extracted = [extract_native_page_image(doc, index) for index in range(doc.page_count)]
    finally:
        doc.close()

    assert all(item is not None for item in extracted)
    assert all(item.width == 600 and item.height == 850 for item in extracted)
