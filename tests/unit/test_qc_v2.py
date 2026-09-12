from pathlib import Path
import fitz

from backend.engine.pipeline_v2.report import ProcessingReport
from backend.engine.qc_v2.validator import validate_output


def _pdf(path: Path, *, pages=2, width=300, height=400, altered=False):
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=width, height=height)
        p.insert_text((40, 60), f'BASE PAGE {i}')
        if altered:
            p.draw_rect(fitz.Rect(0, 0, width * .7, height * .7), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(path); doc.close()


def _report(path: Path, regions=None):
    return ProcessingReport('stream_remove', .96, str(path), metadata={'watermark_regions': regions or []})


def test_qc_rejects_page_count_and_geometry_loss(tmp_path):
    src = tmp_path/'src.pdf'; _pdf(src, pages=2)
    fewer = tmp_path/'fewer.pdf'; _pdf(fewer, pages=1)
    q1 = validate_output(src, fewer, _report(fewer))
    assert not q1.ok and not q1.page_count_ok

    wrong = tmp_path/'wrong.pdf'; _pdf(wrong, pages=2, width=350)
    q2 = validate_output(src, wrong, _report(wrong))
    assert not q2.ok and not q2.geometry_ok


def test_qc_rejects_excessive_changes_outside_watermark_regions(tmp_path):
    src = tmp_path/'src.pdf'; out = tmp_path/'out.pdf'
    _pdf(src); _pdf(out, altered=True)
    report = _report(out, regions=[(0.9, 0.9, 1.0, 1.0)])
    qc = validate_output(src, out, report, max_outside_change_ratio=0.03)
    assert not qc.ok
    assert qc.outside_change_ratio > 0.03


def _raster_pdf(path: Path, *, changed=False):
    import io
    import numpy as np
    from PIL import Image

    doc = fitz.open()
    for page_index in range(2):
        image = np.full((1200, 900, 3), 248, dtype=np.uint8)
        image[120:126, 80:820] = 20
        if changed:
            image[700:760, 620:800] = 235
        bio = io.BytesIO()
        Image.fromarray(image).save(bio, format='PNG')
        page = doc.new_page(width=450, height=600)
        page.insert_image(page.rect, stream=bio.getvalue())
    doc.save(path)
    doc.close()


def test_qc_uses_native_full_page_images_without_page_render(tmp_path, monkeypatch):
    src = tmp_path / 'src-raster.pdf'
    out = tmp_path / 'out-raster.pdf'
    _raster_pdf(src, changed=False)
    _raster_pdf(out, changed=True)
    report = _report(out, regions=[(0.65, 0.55, 0.95, 0.75)])

    monkeypatch.setattr(
        fitz.Page,
        'get_pixmap',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('page render forbidden for native raster QC')),
    )
    qc = validate_output(src, out, report, max_outside_change_ratio=0.001, dpi=96)
    assert qc.ok
    assert qc.outside_change_ratio == 0.0


def test_qc_rejects_visible_watermark_residual_even_when_outside_change_is_zero(tmp_path):
    src=tmp_path/'src.pdf'; out=tmp_path/'out.pdf'; _pdf(src); _pdf(out)
    report = ProcessingReport(
        'raster_template', .98, str(out),
        metadata={'native_outside_change_ratio': 0.0, 'watermark_residual_score': 0.12},
    )
    qc=validate_output(src,out,report,max_outside_change_ratio=0.08,max_watermark_residual_score=0.08,dpi=72)
    assert not qc.ok
    assert qc.watermark_residual_score == 0.12
    assert any('watermark residual' in reason for reason in qc.reasons)


def test_qc_accepts_low_native_residual_without_relaxing_outside_change_gate(tmp_path):
    src=tmp_path/'src.pdf'; out=tmp_path/'out.pdf'; _pdf(src); _pdf(out)
    report = ProcessingReport(
        'raster_template', .98, str(out),
        metadata={'native_outside_change_ratio': 0.0, 'watermark_residual_score': 0.01},
    )
    qc=validate_output(src,out,report,max_outside_change_ratio=0.001,max_watermark_residual_score=0.08,dpi=72)
    assert qc.ok
    assert qc.outside_change_ratio == 0.0
    assert qc.watermark_residual_score == 0.01
