from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageDraw


def make_vector_overlay_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Base lesson content page {index + 1}", fontsize=14)
        page.insert_text((330, 760), "TAILIEUONTHI.NET", fontsize=12, color=(0.7, 0.7, 0.7))
    doc.save(path)
    doc.close()
    return path


def _png_bytes(label: str, size: tuple[int, int] = (600, 850)) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 80), label, fill="black")
    draw.text((300, 720), "TAILIEUONTHI.NET", fill=(190, 190, 190))
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def make_raster_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=600, height=850)
        page.insert_image(page.rect, stream=_png_bytes(f"Raster lesson {index + 1}"))
    doc.save(path)
    doc.close()
    return path


def make_hybrid_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=600, height=850)
    page.insert_text((50, 80), "Selectable text layer", fontsize=14)
    page.insert_image(fitz.Rect(50, 150, 550, 650), stream=_png_bytes("Embedded figure", (500, 500)))
    doc.save(path)
    doc.close()
    return path
