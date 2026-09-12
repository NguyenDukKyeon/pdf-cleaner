from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
import numpy as np
from PIL import Image

from backend.engine.raster.image_extractor import extract_native_page_image


@dataclass(frozen=True, slots=True)
class QCReport:
    ok: bool
    page_count_ok: bool
    geometry_ok: bool
    outside_change_ratio: float
    failed_pages: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()
    changed_pixel_ratio: float = 0.0
    watermark_residual_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _allowed_mask(height: int, width: int, regions: list[tuple[float, float, float, float]]) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    for region in regions:
        if len(region) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in region)
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        ix0, ix1 = int(round(x0 * width)), int(round(x1 * width))
        iy0, iy1 = int(round(y0 * height)), int(round(y1 * height))
        mask[iy0:iy1, ix0:ix1] = True
    return mask


def _render_gray(page: fitz.Page, dpi: int) -> np.ndarray:
    scale = max(72, int(dpi)) / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _native_gray(doc: fitz.Document, page_index: int, *, max_dimension: int = 1200) -> np.ndarray | None:
    native = extract_native_page_image(doc, page_index)
    if native is None:
        return None
    with Image.open(BytesIO(native.image_bytes)) as image:
        gray = image.convert("L")
        width, height = gray.size
        largest = max(width, height)
        if max_dimension > 0 and largest > max_dimension:
            scale = max_dimension / float(largest)
            gray = gray.resize(
                (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                Image.Resampling.BILINEAR,
            )
        return np.asarray(gray, dtype=np.uint8)


def _qc_gray(doc: fitz.Document, page_index: int, dpi: int) -> np.ndarray:
    native = _native_gray(doc, page_index)
    if native is not None:
        return native
    return _render_gray(doc[page_index], dpi)


def validate_output(
    input_pdf: str | Path,
    output_pdf: str | Path,
    report,
    *,
    max_outside_change_ratio: float = 0.08,
    max_watermark_residual_score: float = 0.08,
    pixel_change_threshold: int = 24,
    dpi: int = 96,
) -> QCReport:
    input_pdf = Path(input_pdf)
    output_pdf = Path(output_pdf)
    reasons: list[str] = []
    failed_pages: list[int] = []
    metadata = dict(getattr(report, "metadata", {}) or {})
    raw_regions = metadata.get("watermark_regions") or []
    regions: list[tuple[float, float, float, float]] = []
    for item in raw_regions:
        try:
            if isinstance(item, dict):
                item = (item["x0"], item["y0"], item["x1"], item["y1"])
            regions.append(tuple(float(v) for v in item))
        except Exception:
            continue

    try:
        src = fitz.open(str(input_pdf))
        out = fitz.open(str(output_pdf))
    except Exception as exc:
        return QCReport(False, False, False, 1.0, (), (f"cannot open PDF: {exc}",), 1.0)

    try:
        page_count_ok = src.page_count == out.page_count
        if not page_count_ok:
            reasons.append(f"page count changed: {src.page_count} -> {out.page_count}")
            return QCReport(False, False, False, 1.0, (), tuple(reasons), 1.0)

        geometry_ok = True
        for index in range(src.page_count):
            a, b = src[index].rect, out[index].rect
            if abs(a.width - b.width) > 0.5 or abs(a.height - b.height) > 0.5:
                geometry_ok = False
                failed_pages.append(index)
        if not geometry_ok:
            reasons.append("page geometry changed")
            return QCReport(False, True, False, 1.0, tuple(failed_pages), tuple(reasons), 1.0)

        native_outside = metadata.get("native_outside_change_ratio")
        residual = metadata.get("watermark_residual_score")
        if native_outside is not None and residual is not None:
            outside_ratio = float(native_outside)
            residual_score = float(residual)
            changed_ratio = float(metadata.get("native_changed_pixel_ratio", 0.0) or 0.0)
            if outside_ratio > max_outside_change_ratio:
                reasons.append(
                    f"outside-mask visual change {outside_ratio:.4f} exceeds {max_outside_change_ratio:.4f}"
                )
            if residual_score > max_watermark_residual_score:
                reasons.append(
                    f"watermark residual {residual_score:.4f} exceeds {max_watermark_residual_score:.4f}"
                )
            return QCReport(
                ok=(
                    page_count_ok
                    and geometry_ok
                    and outside_ratio <= max_outside_change_ratio
                    and residual_score <= max_watermark_residual_score
                ),
                page_count_ok=page_count_ok,
                geometry_ok=geometry_ok,
                outside_change_ratio=outside_ratio,
                failed_pages=tuple(sorted(set(failed_pages))),
                reasons=tuple(reasons),
                changed_pixel_ratio=changed_ratio,
                watermark_residual_score=residual_score,
            )

        outside_changed = 0
        outside_total = 0
        changed_total = 0
        pixel_total = 0
        for index in range(src.page_count):
            a = _qc_gray(src, index, dpi)
            b = _qc_gray(out, index, dpi)
            h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
            a, b = a[:h, :w], b[:h, :w]
            changed = np.abs(a.astype(np.int16) - b.astype(np.int16)) >= int(pixel_change_threshold)
            allowed = _allowed_mask(h, w, regions)
            outside = ~allowed
            changed_total += int(changed.sum())
            pixel_total += int(changed.size)
            outside_changed += int((changed & outside).sum())
            outside_total += int(outside.sum())
            ratio = float((changed & outside).sum()) / max(1, int(outside.sum()))
            if ratio > max_outside_change_ratio:
                failed_pages.append(index)

        outside_ratio = outside_changed / max(1, outside_total)
        changed_ratio = changed_total / max(1, pixel_total)
        if outside_ratio > max_outside_change_ratio:
            reasons.append(
                f"outside-mask visual change {outside_ratio:.4f} exceeds {max_outside_change_ratio:.4f}"
            )
        return QCReport(
            ok=page_count_ok and geometry_ok and outside_ratio <= max_outside_change_ratio,
            page_count_ok=page_count_ok,
            geometry_ok=geometry_ok,
            outside_change_ratio=float(outside_ratio),
            failed_pages=tuple(sorted(set(failed_pages))),
            reasons=tuple(reasons),
            changed_pixel_ratio=float(changed_ratio),
        )
    finally:
        src.close()
        out.close()
