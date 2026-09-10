from tests.fixtures_factory import make_vector_overlay_pdf
from backend.engine.analyzer.document_analyzer import analyze_document
from backend.engine.analyzer.models import DocumentKind


def test_vector_pdf_reports_text_layer_and_repeated_watermark_candidate(tmp_path):
    pdf = make_vector_overlay_pdf(tmp_path / "vector.pdf")
    profile = analyze_document(pdf)
    assert profile.kind is DocumentKind.VECTOR
    assert profile.text_layer_ratio == 1.0
    assert profile.full_page_image_ratio == 0.0
    assert any(c.marker == "tailieuonthi" for c in profile.watermark_candidates)
    candidate = next(c for c in profile.watermark_candidates if c.marker == "tailieuonthi")
    assert len(candidate.page_indices) == 3
