from __future__ import annotations
from dataclasses import dataclass

import fitz


@dataclass(frozen=True, slots=True)
class FullPageImage:
    xref: int
    coverage: float


@dataclass(frozen=True, slots=True)
class NativePageImage:
    xref: int
    image_bytes: bytes
    extension: str
    width: int
    height: int
    coverage: float


def find_full_page_image(page, *, min_coverage: float = 0.95) -> FullPageImage | None:
    if not 0.0 < min_coverage <= 1.0:
        raise ValueError("min_coverage must be in (0, 1]")

    page_area = float(page.rect.get_area())
    if page_area <= 0:
        return None

    placements: list[tuple[int, float]] = []
    seen: set[tuple[int, float, float, float, float]] = set()
    for info in page.get_image_info(xrefs=True):
        xref = int(info.get("xref") or 0)
        if xref <= 0:
            continue
        try:
            rect = fitz.Rect(info["bbox"])
        except Exception:
            continue
        key = (
            xref,
            round(float(rect.x0), 3),
            round(float(rect.y0), 3),
            round(float(rect.x1), 3),
            round(float(rect.y1), 3),
        )
        if key in seen:
            continue
        seen.add(key)
        placements.append((xref, min(1.0, float(rect.get_area()) / page_area)))

    if len(placements) != 1:
        return None

    xref, coverage = placements[0]
    if coverage < min_coverage:
        return None
    return FullPageImage(xref=xref, coverage=coverage)


def extract_native_page_image(doc, page_index: int, *, min_coverage: float = 0.95) -> NativePageImage | None:
    if page_index < 0 or page_index >= doc.page_count:
        raise IndexError("page_index out of range")

    found = find_full_page_image(doc[page_index], min_coverage=min_coverage)
    if found is None:
        return None

    payload = doc.extract_image(found.xref)
    image_bytes = payload.get("image")
    if not image_bytes:
        return None

    return NativePageImage(
        xref=found.xref,
        image_bytes=bytes(image_bytes),
        extension=str(payload.get("ext") or "bin").lower(),
        width=int(payload.get("width") or 0),
        height=int(payload.get("height") or 0),
        coverage=found.coverage,
    )
