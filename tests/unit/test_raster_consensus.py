from io import BytesIO
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

from backend.engine.raster.consensus import learn_watermark_model
from backend.engine.raster.sampler import load_native_samples


def _sample(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.full((180, 240, 3), 252, dtype=np.uint8)
    y = 20 + seed * 22
    image[y:y+3, 25:180] = 25
    for x in range(110, 225):
        yy = int(165 - 0.55 * (x - 110))
        image[max(0, yy-2):min(180, yy+3), x:x+2] = 205
    noise = rng.integers(-2, 3, size=image.shape, dtype=np.int16)
    return np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def test_learn_watermark_model_uses_cross_page_consensus():
    samples = [_sample(i) for i in range(5)]
    model = learn_watermark_model(samples, marker_id="tailieuonthi")
    assert model.sample_count == 5
    assert model.mask.shape == samples[0].shape[:2]
    assert model.mask[135:165, 120:205].sum() > 100
    assert model.mask[18:120, 20:185].mean() < 0.12
    assert 0.0 <= model.confidence <= 1.0


def _large_png(seed: int) -> bytes:
    image = np.full((1600, 1200, 3), 252, dtype=np.uint8)
    y = 120 + seed * 180
    image[y:y+8, 80:900] = 20
    for x in range(620, 1120):
        yy = int(1450 - 0.7 * (x - 620))
        image[max(0, yy-5):min(1600, yy+5), x:x+4] = 205
    bio = BytesIO()
    Image.fromarray(image).save(bio, format="PNG")
    return bio.getvalue()


def test_native_consensus_samples_can_be_bounded_without_page_render(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "large-raster.pdf"
    doc = fitz.open()
    for index in range(3):
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=_large_png(index))
    doc.save(source)
    doc.close()

    monkeypatch.setattr(
        fitz.Page,
        "get_pixmap",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("page render forbidden")),
    )

    with fitz.open(source) as opened:
        indices, samples = load_native_samples(opened, max_samples=3, max_dimension=512)

    assert indices == (0, 1, 2)
    assert len(samples) == 3
    assert all(max(sample.shape[:2]) <= 512 for sample in samples)
    assert all(sample.dtype == np.uint8 and sample.shape[2] == 3 for sample in samples)
