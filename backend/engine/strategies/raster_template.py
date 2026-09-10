from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Callable

import fitz
import numpy as np
from PIL import Image

from backend.engine.raster.consensus import RasterWatermarkModel, learn_watermark_model
from backend.engine.raster.image_extractor import extract_native_page_image
from backend.engine.raster.repair import repair_with_model
from backend.engine.raster.sampler import load_native_samples
from backend.engine.router.models import ProcessingPlan, StrategyKind

from .base import StrategyResult


class RasterTemplateStrategy:
    def execute(
        self,
        input_pdf: Path,
        output_pdf: Path,
        plan: ProcessingPlan,
        *,
        log: Callable[[str], None] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> StrategyResult:
        if plan.strategy is not StrategyKind.RASTER_TEMPLATE:
            raise ValueError("RasterTemplateStrategy requires a RASTER_TEMPLATE plan")
        marker_id = next((op.marker for op in plan.operations if op.marker), None)
        doc = fitz.open(str(input_pdf))
        try:
            # Consensus does not need native 300-DPI arrays. Keep representative
            # samples bounded; repair below still operates on the original XObject.
            _, samples = load_native_samples(doc, max_samples=8, max_dimension=900)
            if len(samples) < 2:
                raise RuntimeError("not enough native raster samples for document-level template")
            model = learn_watermark_model(samples, marker_id=marker_id)
            if model.confidence < 0.55 or not model.mask.any():
                raise RuntimeError("document-level raster watermark consensus is too weak")
            if log:
                log(f"Raster consensus: samples={len(samples)} confidence={model.confidence:.3f}")
            changed_pages = changed_pixels = native_pages = 0
            processed_xrefs: set[int] = set()
            total = doc.page_count
            for index in range(total):
                if should_cancel and should_cancel():
                    raise RuntimeError("cancelled")
                native = extract_native_page_image(doc, index)
                if native is None:
                    raise RuntimeError(f"page {index + 1} is not a conservative full-page native image")
                native_pages += 1
                if native.xref in processed_xrefs:
                    if progress:
                        progress(index + 1, total, "native image reused")
                    continue
                with Image.open(BytesIO(native.image_bytes)) as im:
                    rgb = np.array(im.convert("RGB"))
                active_model = model
                if model.mask.shape != rgb.shape[:2]:
                    import cv2

                    mask = cv2.resize(
                        model.mask.astype(np.uint8),
                        (rgb.shape[1], rgb.shape[0]),
                        interpolation=cv2.INTER_NEAREST,
                    ) > 0
                    template = cv2.resize(
                        model.template_gray.astype(np.float32),
                        (rgb.shape[1], rgb.shape[0]),
                        interpolation=cv2.INTER_LINEAR,
                    )
                    active_model = RasterWatermarkModel(
                        mask,
                        template,
                        model.confidence,
                        model.sample_count,
                        model.marker_id,
                    )
                cleaned, pixels = repair_with_model(rgb, active_model)
                changed_pixels += pixels
                if pixels:
                    bio = BytesIO()
                    Image.fromarray(cleaned).save(bio, format="PNG", optimize=False)
                    doc[index].replace_image(native.xref, stream=bio.getvalue())
                    changed_pages += 1
                processed_xrefs.add(native.xref)
                if progress:
                    progress(index + 1, total, "applying document watermark model")
            output_pdf = Path(output_pdf)
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(output_pdf), garbage=4, deflate=True)
            return StrategyResult(
                removed_items=changed_pixels,
                changed_pages=changed_pages,
                rasterized_pages=0,
                saved_to=str(output_pdf),
                native_image_pages=native_pages,
                ocr_calls=0,
            )
        finally:
            doc.close()
