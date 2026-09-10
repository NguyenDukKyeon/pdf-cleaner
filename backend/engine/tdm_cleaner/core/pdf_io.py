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
        page_rect.x0 + (x + w)