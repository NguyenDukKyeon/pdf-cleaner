import numpy as np
from backend.engine.raster.consensus import RasterWatermarkModel
from backend.engine.raster.repair import repair_with_model


def test_repair_preserves_dark_formula_strokes():
    image = np.full((100, 140, 3), 250, dtype=np.uint8)
    image[35:70, 45:115] = 205
    image[50:53, 25:125] = 15
    mask = np.zeros((100, 140), dtype=bool)
    mask[35:70, 45:115] = True
    model = RasterWatermarkModel(mask=mask, template_gray=np.full((100, 140), 205, dtype=np.float32), confidence=.9, sample_count=5)
    cleaned, changed = repair_with_model(image, model)
    assert changed > 0
    assert cleaned[51, 60].mean() < 40
    assert cleaned[40, 60].mean() > image[40, 60].mean() + 20
