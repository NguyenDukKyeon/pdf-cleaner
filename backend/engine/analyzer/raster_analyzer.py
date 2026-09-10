from __future__ import annotations

from hashlib import sha256


def full_page_image_coverage(page) -> float:
    page_area = float(page.rect.get_area())
    if page_area <= 0:
        return 0.0
    best = 0.0
    for image in page.get_images(full=True):
        xref = image[0]
        for rect in page.get_image_rects(xref):
            best = max(best, min(1.0, float(rect.get_area()) / page_area))
    return best


def page_image_hashes(doc, page) -> tuple[str, ...]:
    hashes = []
    for image in page.get_images(full=True):
        xref = image[0]
        try:
            payload = doc.extract_image(xref)["image"]
        except Exception:
            continue
        hashes.append(sha256(payload).hexdigest())
    return tuple(hashes)
