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


def test_raster_template_strategy_learns_once_and_uses_native_images(tmp_path, monkeypatch):
    src = tmp_path / 'src.pdf'; out = tmp_path / 'out.pdf'; _make_pdf(src)
    monkeypatch.setattr(fitz.Page, 'get_pixmap', lambda *a, **k: (_ for _ in ()).throw(AssertionError('render forbidden')))
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
