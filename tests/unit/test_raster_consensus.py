import numpy as np
from backend.engine.raster.consensus import learn_watermark_model


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
