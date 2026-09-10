from __future__ import annotations

from io import BytesIO
import fitz
import numpy as np
from PIL import Image
from backend.engine.analyzer.document_analyzer import select_sample_pages
from .image_extractor import extract_native_page_image


def load_native_samples(doc: fitz.Document, *, max_samples: int = 8) -> tuple[tuple[int, ...], list[np.ndarray]]:
    indices = select_sample_pages(doc.page_count, max_samples=max_samples)
    images = []; kept = []
    for index in indices:
        native = extract_native_page_image(doc, index)
        if native is None:
            continue
        with Image.open(BytesIO(native.image_bytes)) as im:
            images.append(np.array(im.convert("RGB")))
        kept.append(index)
    return tuple(kept), images
