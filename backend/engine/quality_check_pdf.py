# -*- coding: utf-8 -*-
"""
quality_check_pdf.py - visual + structure regression check.

Usage:
    python quality_check_pdf.py input.pdf output.pdf --dpi 150 --csv report.csv

Checks:
- page_count/page_size preservation
- extractable text preservation when the selected pipeline should keep text
- image/drawing object counts as hints
- rendered dark/high-contrast stroke loss ratios
- global mean pixel diff

The implementation is streaming/page-by-page and opens each PDF once per run.
"""
from __future__ import annotations

import argparse
import csv
import unicodedata
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

_MATRIX_CACHE: dict[int, fitz.Matrix] = {}


def _render_matrix(dpi: int) -> fitz.Matrix:
    dpi = int(dpi)
    matrix = _MATRIX_CACHE.get(dpi)
    if matrix is None:
        zoom = float(dpi) / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        _MATRIX_CACHE[dpi] = matrix
    return matrix


def render_doc_page_rgb(doc: fitz.Document, page_index: int, dpi: int) -> tuple[np.ndarray, tuple[float, float]]:
    page = doc.load_page(page_index)
    size = (round(float(page.rect.width), 4), round(float(page.rect.height), 4))
    pix = page.get_pixmap(matrix=_render_matrix(dpi), colorspace=fitz.csRGB, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    elif pix.n > 3:
        arr = arr[:, :, :3]
    return arr.copy(), size


def render_page_rgb(path: Path, page_index: int, dpi: int) -> tuple[np.ndarray, tuple[float, float]]:
    """Backward-compatible helper used by external scripts."""
    doc = fitz.open(str(path))
    try:
        return render_doc_page_rgb(doc, page_index, dpi)
    finally:
        doc.close()


def high_contrast_mask(gray: np.ndarray) -> np.ndarray:
    bg = cv2.medianBlur(gray, 31)
    contrast = bg.astype(np.int16) - gray.astype(np.int16)
    dark = gray <= 125
    high_contrast = contrast >= 35
    edges = cv2.Canny(gray, 30, 120) > 0
    return dark & (high_contrast | edges)


def page_structure(doc: fitz.Document, page_index: int) -> dict[str, int]:
    page = doc.load_page(page_index)
    text = page.get_text("text") or ""
    try:
        drawings = len(page.get_drawings())
    except Exception:
        drawings = -1
    try:
        images = len(page.get_images(full=True))
    except Exception:
        images = -1
    return {"text_chars": len(text.strip()), "drawings": drawings, "images": images}


def _marker_key(value: str) -> str:
    """Normalize watermark text so spacing/diacritics/punctuation do not matter."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch.lower() for ch in without_marks if ch.isalnum())


def _text_preservation_baseline(text: str, allowed_removed_text_markers: list[str] | None) -> tuple[int, int]:
    """Return source text chars that must remain, excluding approved watermark lines."""
    markers = [_marker_key(item) for item in (allowed_removed_text_markers or [])]
    markers = [item for item in markers if item]
    if not markers:
        chars = len((text or "").strip())
        return chars, 0

    kept_lines: list[str] = []
    removed_chars = 0
    for line in (text or "").splitlines():
        normalized_line = _marker_key(line)
        if any(marker in normalized_line for marker in markers):
            removed_chars += len(line)
            continue
        kept_lines.append(line)
    return len("\n".join(kept_lines).strip()), removed_chars


def write_csv_report(rows: list[dict[str, Any]], csv_path: Path) -> None:
    fieldnames = list(rows[0].keys()) if rows else []
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def _sample_page_indices(page_count: int, mode: str, quick_max_pages: int) -> list[int]:
    if page_count <= 0:
        return []
    mode = str(mode or "full").strip().lower()
    if mode != "quick":
        return list(range(page_count))
    limit = max(1, int(quick_max_pages or 1))
    if page_count <= limit:
        return list(range(page_count))
    if limit == 1:
        return [0]
    return sorted({round(i * (page_count - 1) / (limit - 1)) for i in range(limit)})


def summarize_rows(
    rows: list[dict[str, Any]],
    page_count_ok: bool,
    *,
    pages_total: int | None = None,
    mode: str = "full",
    workers: int = 1,
    fail_on_visual_loss: bool = True,
    require_text_preservation: bool = True,
) -> dict[str, Any]:
    structural_failed_pages = [
        int(r["page"])
        for r in rows
        if not bool(r["page_count_ok"])
        or not bool(r["page_size_ok"])
        or not bool(r["text_preserved"])
    ]
    dark_warnings = [int(r["page"]) for r in rows if bool(r.get("dark_loss_exceeded"))]
    hc_warnings = [int(r["page"]) for r in rows if bool(r.get("high_contrast_loss_exceeded"))]
    mean_diff_warnings = [int(r["page"]) for r in rows if bool(r.get("mean_abs_diff_exceeded"))]
    visual_warning_pages = sorted(set(dark_warnings + hc_warnings + mean_diff_warnings))
    visual_failed_pages = visual_warning_pages if fail_on_visual_loss else []
    failed_pages = sorted(set(structural_failed_pages + visual_failed_pages))
    text_layer_removed_pages = [int(r["page"]) for r in rows if bool(r.get("text_layer_removed"))]
    text_loss_allowed_pages = [int(r["page"]) for r in rows if bool(r.get("text_loss_allowed"))]
    requested_workers = max(1, int(workers or 1))
    return {
        "page_count_ok": bool(page_count_ok),
        "pages_total": int(pages_total if pages_total is not None else len(rows)),
        "pages_checked": len(rows),
        "sampled_pages": [int(r["page"]) for r in rows],
        "qc_mode": str(mode or "full").lower(),
        # QC is deliberately sequential: each PDF stays open once, which is safer
        # and usually faster than reopening large PDFs in multiple workers.
        "qc_workers": 1,
        "qc_workers_requested": requested_workers,
        "text_preservation_required": bool(require_text_preservation),
        "text_layer_removed_pages": text_layer_removed_pages,
        "text_loss_allowed_pages": text_loss_allowed_pages,
        "structural_failed_pages": structural_failed_pages,
        "visual_warning_pages": visual_warning_pages,
        "visual_failed_pages": visual_failed_pages,
        "dark_loss_warning_pages": dark_warnings,
        "high_contrast_loss_warning_pages": hc_warnings,
        "mean_abs_diff_warning_pages": mean_diff_warnings,
        "failed_pages": failed_pages,
        "ok": bool(page_count_ok and not failed_pages),
    }


def run_quality_check(
    input_pdf: Path,
    output_pdf: Path,
    *,
    dpi: int = 150,
    csv_path: Path | None = None,
    fail_on_visual_loss: bool = True,
    dark_lost_ratio_max: float = 0.025,
    high_contrast_lost_ratio_max: float = 0.025,
    mean_abs_diff_max: float | None = None,
    workers: int = 1,
    mode: str = "full",
    quick_max_pages: int = 12,
    require_text_preservation: bool = True,
    allowed_removed_text_markers: list[str] | None = None,
    allowed_text_loss_pages: list[int] | None = None,
) -> dict[str, Any]:
    """Run QC page-by-page and return rows plus a decision summary.

    ``require_text_preservation`` must be false for intentional full-raster
    pipelines. The removed text layer is still reported as a warning, but it no
    longer rejects an otherwise valid raster output.

    ``allowed_removed_text_markers`` is for stream-safe cleaners such as Hóa/TYHH:
    source lines containing one of these watermark markers are excluded from the
    text-preservation baseline, while all other source text remains protected.

    ``allowed_text_loss_pages`` is for mixed hybrid/raster pipelines. Text loss is
    accepted only on pages the processing pipeline explicitly reports as rasterized.
    """
    input_pdf = Path(input_pdf).expanduser().resolve()
    output_pdf = Path(output_pdf).expanduser().resolve()
    dpi = max(72, min(int(dpi), 600))
    mode = str(mode or "full").strip().lower()
    if mode not in {"full", "quick"}:
        mode = "full"
    dark_lost_ratio_max = max(0.0, float(dark_lost_ratio_max))
    high_contrast_lost_ratio_max = max(0.0, float(high_contrast_lost_ratio_max))
    if mean_abs_diff_max is not None:
        mean_abs_diff_max = max(0.0, float(mean_abs_diff_max))
    allowed_text_loss_page_set = {int(page) for page in (allowed_text_loss_pages or []) if int(page) > 0}

    src = fitz.open(str(input_pdf))
    dst = fitz.open(str(output_pdf))
    rows: list[dict[str, Any]] = []
    source_page_count = len(src)
    output_page_count = len(dst)
    pages_total = max(source_page_count, output_page_count)
    try:
        page_count_ok = source_page_count == output_page_count
        common_page_count = min(source_page_count, output_page_count)
        page_indices = _sample_page_indices(common_page_count, mode, quick_max_pages)
        for i in page_indices:
            before, before_size = render_doc_page_rgb(src, i, dpi)
            after, after_size = render_doc_page_rgb(dst, i, dpi)
            if before.shape != after.shape:
                h = min(before.shape[0], after.shape[0])
                w = min(before.shape[1], after.shape[1])
                before = before[:h, :w]
                after = after[:h, :w]

            gb = cv2.cvtColor(before, cv2.COLOR_RGB2GRAY)
            ga = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY)
            dark_before = gb < 90
            dark_lost = dark_before & ((ga.astype(np.int16) - gb.astype(np.int16)) > 45)
            hc = high_contrast_mask(gb)
            high_contrast_lost = hc & ((ga.astype(np.int16) - gb.astype(np.int16)) > 45)
            src_s = page_structure(src, i)
            dst_s = page_structure(dst, i)
            src_text = src.load_page(i).get_text("text") or ""
            required_text_chars, allowed_removed_text_chars = _text_preservation_baseline(
                src_text,
                allowed_removed_text_markers,
            )

            text_layer_removed = False
            if required_text_chars > 0:
                text_layer_removed = dst_s["text_chars"] < int(required_text_chars * 0.98)
            text_loss_allowed = (i + 1) in allowed_text_loss_page_set
            text_preserved = (not require_text_preservation) or text_loss_allowed or (not text_layer_removed)

            mean_abs_diff = float(np.mean(np.abs(gb.astype(np.int16) - ga.astype(np.int16))))
            dark_lost_ratio = float(np.count_nonzero(dark_lost) / max(1, np.count_nonzero(dark_before)))
            high_contrast_lost_ratio = float(np.count_nonzero(high_contrast_lost) / max(1, np.count_nonzero(hc)))
            dark_loss_exceeded = dark_lost_ratio > dark_lost_ratio_max
            high_contrast_loss_exceeded = high_contrast_lost_ratio > high_contrast_lost_ratio_max
            mean_abs_diff_exceeded = mean_abs_diff_max is not None and mean_abs_diff > mean_abs_diff_max

            rows.append({
                "page": i + 1,
                "page_count_ok": page_count_ok,
                "page_size_ok": before_size == after_size,
                "before_text_chars": src_s["text_chars"],
                "required_text_chars": required_text_chars,
                "allowed_removed_text_chars": allowed_removed_text_chars,
                "after_text_chars": dst_s["text_chars"],
                "text_layer_removed": text_layer_removed,
                "text_loss_allowed": text_loss_allowed,
                "text_preserved": text_preserved,
                "before_images": src_s["images"],
                "after_images": dst_s["images"],
                "before_drawings": src_s["drawings"],
                "after_drawings": dst_s["drawings"],
                "mean_abs_diff": round(mean_abs_diff, 3),
                "mean_abs_diff_exceeded": bool(mean_abs_diff_exceeded),
                "dark_pixels_before": int(np.count_nonzero(dark_before)),
                "dark_lost_pixels": int(np.count_nonzero(dark_lost)),
                "dark_lost_ratio": round(dark_lost_ratio, 5),
                "dark_loss_exceeded": bool(dark_loss_exceeded),
                "high_contrast_pixels_before": int(np.count_nonzero(hc)),
                "high_contrast_lost_pixels": int(np.count_nonzero(high_contrast_lost)),
                "high_contrast_lost_ratio": round(high_contrast_lost_ratio, 5),
                "high_contrast_loss_exceeded": bool(high_contrast_loss_exceeded),
            })
    finally:
        src.close()
        dst.close()

    if csv_path is not None:
        write_csv_report(rows, Path(csv_path))

    return {
        "rows": rows,
        "summary": summarize_rows(
            rows,
            page_count_ok,
            pages_total=pages_total,
            mode=mode,
            workers=workers,
            fail_on_visual_loss=bool(fail_on_visual_loss),
            require_text_preservation=bool(require_text_preservation),
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input_pdf", type=Path)
    p.add_argument("output_pdf", type=Path)
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--csv", type=Path, default=None)
    p.add_argument("--quick", action="store_true", help="Check a representative page sample instead of every page.")
    p.add_argument("--quick-max-pages", type=int, default=12)
    p.add_argument("--allow-raster-text-loss", action="store_true", help="Do not fail when an intentional raster output has no text layer.")
    p.add_argument("--fail-on-visual-loss", action="store_true", help="Treat configured visual threshold warnings as QC failures.")
    p.add_argument("--fail-on-structural-loss", action="store_true", help="Return exit code 2 when structural QC fails.")
    args = p.parse_args()

    report = run_quality_check(
        args.input_pdf,
        args.output_pdf,
        dpi=args.dpi,
        csv_path=args.csv,
        mode="quick" if args.quick else "full",
        quick_max_pages=args.quick_max_pages,
        require_text_preservation=not args.allow_raster_text_loss,
        fail_on_visual_loss=args.fail_on_visual_loss,
    )
    if args.csv:
        print(f"Saved: {args.csv}")
    else:
        for row in report["rows"]:
            print(row)
    print("Summary:", report["summary"])
    if args.fail_on_structural_loss and not report["summary"].get("ok", False):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
