from __future__ import annotations

from pathlib import Path
import os
import tempfile

import cv2
import fitz
import numpy as np

from .models import PageResult, PagePatch, VectorLineSegment

_DOC_CACHE: dict[str, fitz.Document] = {}
_MATRIX_CACHE: dict[int, fitz.Matrix] = {}


def get_render_matrix(dpi: int) -> fitz.Matrix:
    """Cache render matrices per process for repeated page rendering."""
    dpi = int(dpi)
    matrix = _MATRIX_CACHE.get(dpi)
    if matrix is None:
        zoom = float(dpi) / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        _MATRIX_CACHE[dpi] = matrix
    return matrix


def get_cached_doc(pdf_path: Path) -> fitz.Document:
    """Open a PDF once per worker process and reuse it for page rendering."""
    key = str(pdf_path.resolve())
    doc = _DOC_CACHE.get(key)
    if doc is None or doc.is_closed:
        doc = fitz.open(key)
        _DOC_CACHE[key] = doc
    return doc


def close_cached_docs() -> None:
    """Close PDF handles cached inside the current process.

    This is a performance helper, not a processing step: pages rendered from the
    cached document are identical to pages rendered from a freshly opened PDF.
    Closing the cache before an in-place replace keeps Windows from holding a
    read lock on the original PDF.
    """
    for doc in list(_DOC_CACHE.values()):
        try:
            if doc is not None and not doc.is_closed:
                doc.close()
        except Exception:
            pass
    _DOC_CACHE.clear()


def read_pdf_page_count(pdf_path: Path) -> int:
    doc = fitz.open(str(pdf_path))
    try:
        return int(len(doc))
    finally:
        doc.close()


def render_page_rgb(
    pdf_path: Path,
    page_index: int,
    dpi: int,
    *,
    use_doc_cache: bool = True,
) -> tuple[np.ndarray, float, float]:
    """Render one PDF page to a writable RGB uint8 array."""
    doc: fitz.Document | None = None
    close_doc = False
    if use_doc_cache:
        doc = get_cached_doc(pdf_path)
    else:
        doc = fitz.open(str(pdf_path))
        close_doc = True

    try:
        page = doc.load_page(int(page_index))
        width_pt = float(page.rect.width)
        height_pt = float(page.rect.height)
        pix = page.get_pixmap(matrix=get_render_matrix(int(dpi)), colorspace=fitz.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 1:
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
        elif pix.n >= 4:
            arr = arr[:, :, :3]
        return arr.copy(), width_pt, height_pt
    finally:
        if close_doc and doc is not None:
            doc.close()


def px_rect_to_pdf_rect(x: int, y: int, w: int, h: int, img_w: int, img_h: int, page_rect: fitz.Rect) -> fitz.Rect:
    """Map image pixel rect (origin top-left) to PDF points inside page.rect."""
    sx = page_rect.width / max(1, float(img_w))
    sy = page_rect.height / max(1, float(img_h))
    return fitz.Rect(
        page_rect.x0 + x * sx,
        page_rect.y0 + y * sy,
        page_rect.x0 + (x + w) * sx,
        page_rect.y0 + (y + h) * sy,
    )


def _insert_patch(page: fitz.Page, patch: PagePatch, result: PageResult) -> None:
    rect = px_rect_to_pdf_rect(patch.x_px, patch.y_px, patch.w_px, patch.h_px, result.img_w, result.img_h, page.rect)
    page.insert_image(rect, stream=patch.image_bytes, keep_proportion=False, overlay=True)


def _draw_vector_line(page: fitz.Page, seg: VectorLineSegment, result: PageResult) -> None:
    sx = page.rect.width / max(1, float(result.img_w))
    sy = page.rect.height / max(1, float(result.img_h))
    p0 = fitz.Point(page.rect.x0 + seg.x0_px * sx, page.rect.y0 + seg.y0_px * sy)
    p1 = fitz.Point(page.rect.x0 + seg.x1_px * sx, page.rect.y0 + seg.y1_px * sy)
    width_pt = max(0.1, float(seg.width_px) * (sx + sy) * 0.5)
    color = tuple(float(max(0.0, min(1.0, c))) for c in seg.rgb)
    try:
        shape = page.new_shape()
        shape.draw_line(p0, p1)
        dashes = "[1 2] 0" if seg.dashed else None
        try:
            shape.finish(color=color, width=width_pt, dashes=dashes, stroke_opacity=float(seg.opacity))
        except TypeError:
            shape.finish(color=color, width=width_pt, dashes=dashes)
        shape.commit(overlay=True)
    except Exception:
        try:
            page.draw_line(p0, p1, color=color, width=width_pt, overlay=True)
        except Exception:
            pass


def insert_page(out_doc: fitz.Document, result: PageResult) -> None:
    """Legacy-compatible full-raster insertion."""
    page = out_doc.new_page(width=result.width_pt, height=result.height_pt)
    if not result.image_bytes:
        raise RuntimeError("Full raster result không có image_bytes.")
    page.insert_image(page.rect, stream=result.image_bytes, keep_proportion=False)


def insert_page_result(out_doc: fitz.Document, src_doc: fitz.Document | None, result: PageResult) -> None:
    """Insert a processed result, preserving the source page for hybrid mode."""
    if result.mode == "hybrid":
        if src_doc is None:
            raise RuntimeError("Hybrid result cần source PDF document để giữ layout gốc.")
        out_doc.insert_pdf(src_doc, from_page=result.page_index, to_page=result.page_index)
        page = out_doc[-1]
        for patch in result.patches:
            _insert_patch(page, patch, result)
        for seg in result.vector_lines:
            _draw_vector_line(page, seg, result)
        return
    insert_page(out_doc, result)


def save_pdf_atomic(out_doc: fitz.Document, output_pdf: Path, *, replace: bool = True) -> Path:
    """Write PDF through a temporary file and optionally replace the final path.

    For normal exports, ``replace=True`` saves to a sibling temporary PDF and then
    atomically replaces ``output_pdf``.  For true in-place overwrite, callers can
    pass ``replace=False``, close every document handle that is still reading the
    original file, then call ``os.replace(tmp_path, output_pdf)`` themselves.
    This avoids Windows failing with PermissionError while the source PDF is open.
    """
    output_pdf = output_pdf.expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=output_pdf.stem + "_", suffix=".tmp.pdf", dir=str(output_pdf.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        out_doc.save(str(tmp_path), garbage=3, deflate=True)
        if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
            raise RuntimeError(f"File tạm rỗng sau khi save: {tmp_path}")
        if replace:
            os.replace(str(tmp_path), str(output_pdf))
            return output_pdf
        return tmp_path
    finally:
        if replace and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
