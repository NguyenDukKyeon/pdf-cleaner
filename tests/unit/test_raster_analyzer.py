from tests.fixtures_factory import make_hybrid_pdf, make_raster_pdf
from backend.engine.analyzer.document_analyzer import analyze_document
from backend.engine.analyzer.models import DocumentKind


def test_full_page_image_document_is_classified_as_raster(tmp_path):
    pdf = make_raster_pdf(tmp_path / "raster.pdf")
    profile = analyze_document(pdf)
    assert profile.kind is DocumentKind.RASTER
    assert profile.text_layer_ratio == 0.0
    assert profile.full_page_image_ratio == 1.0


def test_mixed_text_and_large_image_document_is_hybrid(tmp_path):
    pdf = make_hybrid_pdf(tmp_path / "hybrid.pdf")
    profile = analyze_document(pdf)
    assert profile.kind is DocumentKind.HYBRID
    assert profile.text_layer_ratio == 1.0
    assert 0.0 < profile.full_page_image_ratio < 1.0
