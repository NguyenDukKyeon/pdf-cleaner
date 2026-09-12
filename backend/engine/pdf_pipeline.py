"""Compatibility wrapper for the refactored PDF pipeline package."""

from tdm_cleaner.core.pipeline import process_pdf_optimized
from tdm_cleaner.core.worker_pool import auto_worker_count
from tdm_cleaner.core.image_ops import clamp_int, effective_render_dpi
from tdm_cleaner.core.pdf_io import read_pdf_page_count

__all__ = ["process_pdf_optimized", "auto_worker_count", "clamp_int", "effective_render_dpi", "read_pdf_page_count"]
