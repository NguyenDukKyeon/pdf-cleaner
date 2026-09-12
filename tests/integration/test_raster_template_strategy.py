from pathlib import Path
import fitz
import numpy as np
from PIL import Image
import io
from backend.engine.router.models import ProcessingOperation, ProcessingPlan, StrategyKind
from backend.engine.strategies.raster_template import RasterTemplateStrategy


def _png_page(seed: int) -> bytes:
    a = np.full((180, 240, 3), 252, dtype=np.uint8)
    y = 20 + seed * 22
    a[y:y+3, 20:180] = 20
    for x in range(110, 225):
        yy = int(165 - .55 * (x - 110))
        a[max(0, yy-2):min(180, yy+3), x:x+2] = 205
    bio = io.BytesIO(); Image.fromarray(a).save(bio, format='PNG'); return bio.getvalue()


def _make_pdf(path: Path):
    doc = fitz.open()
    for i in range(5):
        p = doc.new_page(width=240, height=180)
        p.insert_image(p.rect, stream=_png_page(i))
    doc.save(path); doc.close()


def _make_large_compressible_pdf(path: Path) -> None:
    doc = fitz.open()
    for seed in range(5):
        image = np.full((1200, 900, 3), 252, dtype=np.uint8)
        image[80 + seed * 140 : 84 + seed * 140, 80:700] = 20
        for x in range(420, 850):
            y = int(1100 - 0.65 * (x - 420))
            image[max(0, y - 5) : min(1200, y + 6), x : x + 3] = 200
        payload = io.BytesIO()
        Image.fromarray(image).save(payload, format='PNG', compress_level=6)
        page = doc.new_page(width=450, height=600)
        page.insert_image(page.rect, stream=payload.getvalue())
    doc.save(path, deflate=True); doc.close()


def test_raster_template_strategy_learns_once_and_uses_native_images(tmp_path, monkeypatch):
    src = tmp_path / 'src.pdf'; out = tmp_path / 'out.pdf'; _make_pdf(src)
    monkeypatch.setattr(fitz.Page, 'get_pixmap', lambda *a, **k: (_ for _ in ()).throw(AssertionError('render forbidden')))
    monkeypatch.setattr(fitz.Page, 'replace_image', lambda *a, **k: (_ for _ in ()).throw(AssertionError('in-place image replacement forbidden')))
    plan = ProcessingPlan(StrategyKind.RASTER_TEMPLATE, .9, (ProcessingOperation('learn_and_apply_raster_template', 'tailieuonthi'),), True)
    result = RasterTemplateStrategy().execute(src, out, plan)
    assert out.exists(); assert result.rasterized_pages == 0; assert result.native_image_pages == 5
    with fitz.open(src) as a, fitz.open(out) as b:
        assert a.page_count == b.page_count == 5
        assert [tuple(p.rect) for p in a] == [tuple(p.rect) for p in b]
        before = a.extract_image(a[0].get_images(full=True)[0][0])['image']
        after = b.extract_image(b[0].get_images(full=True)[0][0])['image']
    bef = np.array(Image.open(io.BytesIO(before)).convert('RGB'))
    aft = np.array(Image.open(io.BytesIO(after)).convert('RGB'))
    assert aft[125:170, 115:215].mean() > bef[125:170, 115:215].mean()
    assert aft[20:23, 30:150].mean() < 60


def test_tailieuonthi_marker_uses_signature_specific_tdm_guided_cleanup(tmp_path, monkeypatch):
    import shutil
    import backend.engine.strategies.raster_template as raster_module
    from backend.engine.raster.tdm_guided import TdmGuidedResult

    src = tmp_path / 'src.pdf'; out = tmp_path / 'out.pdf'; _make_pdf(src)
    calls = []

    def fake_guided(input_pdf, output_pdf, **kwargs):
        calls.append((Path(input_pdf), Path(output_pdf), kwargs))
        shutil.copyfile(input_pdf, output_pdf)
        return TdmGuidedResult(
            changed_pages=5,
            changed_pixels=123,
            native_image_pages=5,
            watermark_residual_score=0.01,
            outside_change_ratio=0.0,
            work_dpi=200,
        )

    monkeypatch.setattr(raster_module, 'clean_tailieuonthi_document', fake_guided)
    plan = ProcessingPlan(
        StrategyKind.RASTER_TEMPLATE,
        .95,
        (ProcessingOperation('learn_and_apply_raster_template', 'tailieuonthi'),),
        True,
    )

    result = RasterTemplateStrategy().execute(src, out, plan)

    assert len(calls) == 1
    assert result.native_image_pages == 5
    assert result.rasterized_pages == 0
    assert result.ocr_calls == 0
    assert result.metadata['repair_engine'] == 'tdm_guided'
    assert result.metadata['watermark_residual_score'] == 0.01
    assert result.metadata['native_outside_change_ratio'] == 0.0


def test_raster_template_strategy_does_not_explode_output_size(tmp_path):
    src = tmp_path / 'large-compressible.pdf'; out = tmp_path / 'large-compressible-clean.pdf'
    _make_large_compressible_pdf(src)
    plan = ProcessingPlan(StrategyKind.RASTER_TEMPLATE, .9, (ProcessingOperation('learn_and_apply_raster_template', 'tailieuonthi'),), True)
    RasterTemplateStrategy().execute(src, out, plan)
    assert out.stat().st_size <= src.stat().st_size * 4
