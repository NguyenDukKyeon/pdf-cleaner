from __future__ import annotations

from io import BytesIO

import cv2
import fitz
import numpy as np
from PIL import Image

from backend.engine.analyzer.document_analyzer import select_sample_pages

from .image_extractor import extract_native_page_image


def _bound_sample(image: np.ndarray, max_dimension: int | None) -> np.ndarray:
    if not max_dimension or int(max_dimension) <= 0:
        return image
    limit = int(max_dimension)
    height, width = image.shape[:2]
    largest = max(height, width)
    if largest <= limit:
        return image
    scale = limit / float(largest)
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))
    return cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)


def load_native_samples(
    doc: fitz.Document,
    *,
    max_samples: int = 8,
    max_dimension: int | None = None,
) -> tuple[tuple[int, ...], list[np.ndarray]]:
    """Load representative embedded page images without rendering PDF pages.

    ``max_dimension`` bounds only the images used to learn the document-level
    watermark model. Full-resolution native page images are still used during
    repair, so this reduces consensus RAM/CPU without lowering output geometry.
    """

    indices = select_sample_pages(doc.page_count, max_samples=max_samples)
    images: list[np.ndarray] = []
    kept: list[int] = []
    for index in indices:
        native = extract_native_page_image(doc, index)
        if native is None:
            continue
        with Image.open(BytesIO(native.image_bytes)) as im:
            rgb = np.array(im.convert("RGB"))
        images.append(_bound_sample(rgb, max_dimension))
        kept.append(index)
    return tuple(kept), images
