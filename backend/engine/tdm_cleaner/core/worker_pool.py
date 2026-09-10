from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import traceback

import cv2
import numpy as np

from tdm_cleaner.processors.factory import get_processor
from .image_ops import (
    apply_fixed_border_whiteout,
    clamp_int,
    encode_rgb_auto_bytes,
    encode_rgb_to_jpeg_bytes,
    encode_rgb_to_png_bytes,
    encode_rgba_to_png_bytes,
    get_structuring_element_cached,
)
from .models import PagePatch, PageResult, VectorLineSegment
from .pdf_io import get_cached_doc, render_page_rgb

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None

try:
    cv2.setNumThreads(1)
    if hasattr(cv2, "ocl"):
        cv2.ocl.setUseOpenCL(False)
except Exception:
    pass

_PROCESSOR_CACHE: dict[tuple[str, str], Any] = {}
_WORKER_CONTEXT: dict[str, Any] | None = None
_WORKER_PROCESSOR: Any | None = None


def worker_init(opencv_threads: int = 1, worker_context: dict[str, Any] | None = None) -> None:
    """Initializer for ProcessPoolExecutor workers.

    ``worker_context`` stores immutable run settings once per process so each
    submitted page only needs to pickle an integer page index.  The image/PDF
    algorithm still receives the exact same settings as before.
    """
    global _WORKER_CONTEXT, _WORKER_PROCESSOR
    try:
        cv2.setNumThreads(max(1, int(opencv_threads)))
        if hasattr(cv2, "ocl"):
            cv2.ocl.setUseOpenCL(False)
    except Exception:
        pass
    _WORKER_CONTEXT = dict(worker_context or {}) if worker_context else None
    _WORKER_PROCESSOR = None
    if _WORKER_CONTEXT:
        # Warm up the per-process PDF handle and processor cache once.  This avoids
        # repeated open/import/signature overhead during the page loop.
        try:
            get_cached_doc(Path(str(_WORKER_CONTEXT["pdf_path"])))
        except Exception:
            pass
        try:
            _WORKER_PROCESSOR = get_processor(_WORKER_CONTEXT["mode"], _WORKER_CONTEXT["mode_config"])
        except Exception:
            _WORKER_PROCESSOR = None


def json_signature(data: dict[str, Any]) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(sorted(data.items()))


def get_cached_processor(mode: str, mode_config: dict[str, Any]):
    """Reuse expensive legacy processor adapters within each worker process."""
    key = (str(mode).lower(), json_signature(mode_config))
    processor = _PROCESSOR_CACHE.get(key)
    if processor is None:
        processor = get_processor(mode, mode_config)
        _PROCESSOR_CACHE[key] = processor
    return processor


def get_context_processor():
    """Return the processor created once by worker_init, falling back safely."""
    global _WORKER_PROCESSOR
    if not _WORKER_CONTEXT:
        raise RuntimeError("Worker context chưa được khởi tạo.")
    if _WORKER_PROCESSOR is None:
        _WORKER_PROCESSOR = get_cached_processor(_WORKER_CONTEXT["mode"], _WORKER_CONTEXT["mode_config"])
    return _WORKER_PROCESSOR


def estimate_worker_memory_mb(dpi: int) -> int:
    dpi = max(72, int(dpi))
    pixels = (8.5 * dpi) * (11.7 * dpi)
    rgb_mb = pixels * 3 / (1024 * 1024)
    return int(max(220, rgb_mb * 7.5 + 140))


def memory_safe_worker_limit(dpi: int) -> int:
    if psutil is None:
        return 4 if dpi >= 300 else 6
    try:
        available_mb = psutil.virtual_memory().available / (1024 * 1024)
        reserve_mb = 900
        per_worker = estimate_worker_memory_mb(dpi)
        return max(1, int((available_mb - reserve_mb) // per_worker))
    except Exception:
        return 4 if dpi >= 300 else 6


def auto_worker_count(requested: int, page_count: int, dpi: int) -> int:
    """Choose the highest CPU parallelism that remains RAM-safe.

    Older builds used small fixed caps (4-6 workers).  This version lets the
    memory estimator be the main safety gate, so large machines can use more
    worker processes without changing page algorithms.  Explicit user requests
    are still clamped by CPU, page count, and RAM to avoid oversubscription.
    """
    page_count = max(1, int(page_count))
    requested = int(requested or 0)
    cpu = max(1, os.cpu_count() or 2)
    memory_cap = memory_safe_worker_limit(dpi)

    # Optional deployment cap for very constrained systems.  It is a runtime
    # limiter only; leaving it unset uses maximum safe throughput.
    env_cap_raw = os.environ.get("TDM_WORKER_HARD_CAP", "0").strip()
    try:
        env_cap = int(env_cap_raw)
    except Exception:
        env_cap = 0
    hard_cap = env_cap if env_cap > 0 else cpu

    if requested > 0:
        return max(1, min(requested, page_count, cpu, memory_cap, hard_cap))

    # Keep one logical CPU for the GUI/OS when possible, but otherwise use all
    # RAM-safe workers.  ProcessPool avoids Python GIL contention for OpenCV/PyMuPDF.
    cpu_target = max(1, cpu - 1) if cpu > 2 else cpu
    return max(1, min(page_count, cpu_target, memory_cap, hard_cap))


def _bool_cfg(cfg: dict[str, Any], key: str, default: bool) -> bool:
    v = cfg.get(key, default)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(v)


def _float_cfg(cfg: dict[str, Any], key: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    try:
        v = float(cfg.get(key, default))
    except Exception:
        v = float(default)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _int_cfg(cfg: dict[str, Any], key: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        v = int(round(float(cfg.get(key, default))))
    except Exception:
        v = int(default)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v



def _diagonal_band_mask_for_hybrid(h: int, w: int, cfg: dict[str, Any]) -> np.ndarray:
    """V7.6.7 helper: approximate the TDM diagonal watermark ROI in rendered pixels.

    V7.7.4 performance keeps the exact same distance formula and clipping window
    as v7.6.7, but allocates coordinate grids only inside the normalized diagonal
    window instead of the whole page.
    """
    p1 = (float(cfg.get("hybrid_watermark_roi_p1_x", 0.615)), float(cfg.get("hybrid_watermark_roi_p1_y", 0.965)))
    p2 = (float(cfg.get("hybrid_watermark_roi_p2_x", 0.965)), float(cfg.get("hybrid_watermark_roi_p2_y", 0.665)))
    x1 = p1[0] * w; y1 = p1[1] * h
    x2 = p2[0] * w; y2 = p2[1] * h
    vx = x2 - x1; vy = y2 - y1
    denom = max(1e-6, (vx * vx + vy * vy) ** 0.5)
    width_default = max(40, int(round(82 * (w / 1653.0))))
    band_width = _int_cfg(cfg, "hybrid_watermark_roi_patch_width_px", width_default, 5, max(w, h))
    xmin = float(cfg.get("diag_x_min", 0.50)); xmax = float(cfg.get("diag_x_max", 0.998))
    ymin = float(cfg.get("diag_y_min", 0.48)); ymax = float(cfg.get("diag_y_max", 0.998))

    ix1 = max(0, int(xmin * w))
    ix2 = min(w - 1, int(xmax * w))
    iy1 = max(0, int(ymin * h))
    iy2 = min(h - 1, int(ymax * h))
    mask = np.zeros((h, w), dtype=bool)
    if ix2 < ix1 or iy2 < iy1:
        return mask

    yy, xx = np.mgrid[iy1:iy2 + 1, ix1:ix2 + 1]
    # Distance to infinite diagonal line; normalized page window clips the segment.
    dist = np.abs(vy * (xx - x1) - vx * (yy - y1)) / denom
    mask[iy1:iy2 + 1, ix1:ix2 + 1] = dist <= float(band_width)
    return mask


def build_hybrid_change_mask(original_rgb: np.ndarray, processed_rgb: np.ndarray, cfg: dict[str, Any]) -> np.ndarray:
    """Detect pixels that really need a PDF overlay patch.

    The threshold is intentionally low because pale ruled-line reconstruction can
    differ by only a few gray levels.  Morphological closing/dilation turns sparse
    watermark/line edits into stable patch masks without touching untouched PDF
    content in hybrid output.
    """
    diff = cv2.absdiff(original_rgb, processed_rgb)
    max_diff = np.max(diff, axis=2)
    gray_o = cv2.cvtColor(original_rgb, cv2.COLOR_RGB2GRAY)
    gray_p = cv2.cvtColor(processed_rgb, cv2.COLOR_RGB2GRAY)
    gray_diff = cv2.absdiff(gray_o, gray_p)

    thr = _int_cfg(cfg, "hybrid_diff_threshold", 4, 1, 40)
    color_thr = _int_cfg(cfg, "hybrid_color_diff_threshold", max(6, thr + 2), 1, 60)
    mask = (gray_diff >= thr) | (max_diff >= color_thr)

    # Strongly include pixels that became much whiter (watermark removal) or a bit
    # darker (ruled-line restoration), even when color channels differ weakly.
    white_gain = gray_p.astype(np.int16) - gray_o.astype(np.int16)
    line_gain = gray_o.astype(np.int16) - gray_p.astype(np.int16)
    mask |= white_gain >= _int_cfg(cfg, "hybrid_white_gain_threshold", 5, 1, 60)
    mask |= line_gain >= _int_cfg(cfg, "hybrid_line_gain_threshold", 3, 1, 50)

    mask_u8 = mask.astype(np.uint8) * 255
    close_x = _int_cfg(cfg, "hybrid_mask_close_x", 7, 1, 101)
    close_y = _int_cfg(cfg, "hybrid_mask_close_y", 5, 1, 101)
    if close_x > 1 or close_y > 1:
        mask_u8 = cv2.morphologyEx(
            mask_u8,
            cv2.MORPH_CLOSE,
            get_structuring_element_cached(cv2.MORPH_RECT, (close_x, close_y)),
            iterations=1,
        )
    dilate = _int_cfg(cfg, "hybrid_mask_dilate_px", 2, 0, 40)
    if dilate > 0:
        k = 2 * dilate + 1
        mask_u8 = cv2.dilate(mask_u8, get_structuring_element_cached(cv2.MORPH_ELLIPSE, (k, k)), iterations=1)

    # V7.6.7: force the cleaned diagonal watermark background ROI into the hybrid
    # alpha mask.  The image algorithm can remove very faint ghost texture by only
    # a few gray levels; a pure diff mask may miss it, causing the old dirty paper
    # to show through.  This still preserves the original PDF outside watermark ROI.
    if _bool_cfg(cfg, "hybrid_force_watermark_roi_patch", False):
        diag_roi = _diagonal_band_mask_for_hybrid(original_rgb.shape[0], original_rgb.shape[1], cfg)
        # V8.8.9 smart-speed: the forced watermark patch only uses pixels inside
        # diag_roi.  Compute HSV/real-stroke tests on that bounding ROI instead
        # of the whole rendered page.  The boolean expression is exactly the same
        # as V8.8.8 inside diag_roi, so the produced mask is identical while the
        # expensive RGB->HSV conversion touches far fewer pixels.
        ys, xs = np.where(diag_roi)
        if ys.size and xs.size:
            y0 = int(ys.min()); y1 = int(ys.max()) + 1
            x0 = int(xs.min()); x1 = int(xs.max()) + 1
            roi_o = original_rgb[y0:y1, x0:x1]
            roi_p = processed_rgb[y0:y1, x0:x1]
            roi_diag = diag_roi[y0:y1, x0:x1]
            roi_gray_o = gray_o[y0:y1, x0:x1]
            roi_gray_p = gray_p[y0:y1, x0:x1]
            hsv_o = cv2.cvtColor(roi_o, cv2.COLOR_RGB2HSV)
            hsv_p = cv2.cvtColor(roi_p, cv2.COLOR_RGB2HSV)
            sat_o = hsv_o[:, :, 1]
            sat_p = hsv_p[:, :, 1]
            text_gray_max = _int_cfg(cfg, "hybrid_watermark_roi_text_gray_max", 145, 0, 255)
            color_sat_min = _int_cfg(cfg, "hybrid_watermark_roi_color_sat_min", 82, 0, 255)
            color_gray_max = _int_cfg(cfg, "hybrid_watermark_roi_color_gray_max", 235, 0, 255)
            # Include the whole pale/gray watermark band in the hybrid alpha mask.
            # Exclude only real dark/colored strokes, exactly as V8.8.8 did.
            real_stroke = (roi_gray_o <= text_gray_max) | (roi_gray_p <= text_gray_max)
            real_stroke |= ((sat_o >= color_sat_min) & (roi_gray_o <= color_gray_max))
            real_stroke |= ((sat_p >= color_sat_min) & (roi_gray_p <= color_gray_max))
            bg_like_roi = roi_diag & (~real_stroke)
            if np.any(bg_like_roi):
                mask_u8[y0:y1, x0:x1] = cv2.bitwise_or(mask_u8[y0:y1, x0:x1], bg_like_roi.astype(np.uint8) * 255)

    return mask_u8 > 0


def _merge_rects(rects: list[tuple[int, int, int, int]], gap: int, max_rects: int) -> list[tuple[int, int, int, int]]:
    """Merge rectangles with small gaps; deterministic and dependency-free."""
    if not rects:
        return []
    rects = [tuple(map(int, r)) for r in rects]
    changed = True
    while changed and len(rects) > 1:
        changed = False
        out: list[tuple[int, int, int, int]] = []
        used = [False] * len(rects)
        for i, a in enumerate(rects):
            if used[i]:
                continue
            ax0, ay0, ax1, ay1 = a
            used[i] = True
            merged = True
            while merged:
                merged = False
                for j, b in enumerate(rects):
                    if used[j]:
                        continue
                    bx0, by0, bx1, by1 = b
                    overlap_or_near = not (ax1 + gap < bx0 or bx1 + gap < ax0 or ay1 + gap < by0 or by1 + gap < ay0)
                    if overlap_or_near:
                        ax0, ay0, ax1, ay1 = min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)
                        used[j] = True
                        merged = True
                        changed = True
            out.append((ax0, ay0, ax1, ay1))
        rects = out
        if len(rects) <= max_rects:
            # Still allow a second pass if something changed, but do not over-merge.
            pass
    return rects


def _component_rects(mask: np.ndarray, cfg: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    h, w = mask.shape[:2]
    min_area = _int_cfg(cfg, "hybrid_min_component_area", 10, 1, 100000)
    pad = _int_cfg(cfg, "hybrid_patch_padding_px", 8, 0, 200)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    rects: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        ww = int(stats[i, cv2.CC_STAT_WIDTH])
        hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w, x + ww + pad)
        y1 = min(h, y + hh + pad)
        rects.append((x0, y0, x1, y1))
    gap = _int_cfg(cfg, "hybrid_merge_gap_px", 10, 0, 200)
    max_rects = _int_cfg(cfg, "hybrid_max_patches", 120, 1, 2000)
    rects = _merge_rects(rects, gap, max_rects)
    if len(rects) > max_rects:
        # Merge by rows/coarse tiles instead of producing thousands of tiny images.
        rects = _merge_rects(rects, max(gap, 40), max_rects)
    return rects


def _make_patch_png(processed_rgb: np.ndarray, mask: np.ndarray, rect: tuple[int, int, int, int], cfg: dict[str, Any]) -> PagePatch:
    x0, y0, x1, y1 = rect
    crop = processed_rgb[y0:y1, x0:x1]
    local_mask = mask[y0:y1, x0:x1].astype(np.uint8) * 255
    alpha_mode = str(cfg.get("hybrid_patch_alpha", "hard")).strip().lower()
    if alpha_mode in {"none", "opaque", "false"}:
        img_bytes = encode_rgb_to_png_bytes(crop, output_grayscale=False, png_compression=_int_cfg(cfg, 'png_compression', 3, 0, 9))
        return PagePatch(x0, y0, x1 - x0, y1 - y0, img_bytes, has_alpha=False)

    # Include the whole component interior where it was closed/dilated. Hard alpha
    # prevents watermark ghosts from showing through; optional feather only softens
    # the very edge of the patch mask.
    alpha = local_mask
    feather = _int_cfg(cfg, "hybrid_alpha_feather_px", 0, 0, 20)
    if feather > 0:
        k = 2 * feather + 1
        alpha = cv2.GaussianBlur(alpha, (k, k), 0)
        alpha = np.maximum(alpha, local_mask)
    rgba = np.dstack([crop, alpha]).astype(np.uint8)
    return PagePatch(x0, y0, x1 - x0, y1 - y0, encode_rgba_to_png_bytes(rgba, png_compression=_int_cfg(cfg, 'png_compression', 3, 0, 9)), has_alpha=True)


def _horizontal_vector_line_candidates(
    original_rgb: np.ndarray,
    processed_rgb: np.ndarray,
    mask: np.ndarray,
    cfg: dict[str, Any],
) -> tuple[list[VectorLineSegment], np.ndarray]:
    """Convert very simple horizontal ruled-line additions to vector lines.

    This is conservative and optional. For scanned/image-based PDFs, raster patches
    usually match the dotted/faint paper better, so the default is disabled/auto.
    """
    enabled = str(cfg.get("hybrid_vector_line_overlay", "false")).strip().lower()
    if enabled in {"0", "false", "no", "off", "none"}:
        return [], mask

    gray_o = cv2.cvtColor(original_rgb, cv2.COLOR_RGB2GRAY)
    gray_p = cv2.cvtColor(processed_rgb, cv2.COLOR_RGB2GRAY)
    darker = (gray_o.astype(np.int16) - gray_p.astype(np.int16)) >= _int_cfg(cfg, "hybrid_vector_line_gain", 3, 1, 40)
    cand = (mask & darker).astype(np.uint8) * 255
    # Thin horizontal components only.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    lines: list[VectorLineSegment] = []
    remove = np.zeros(mask.shape, dtype=bool)
    min_w = _int_cfg(cfg, "hybrid_vector_line_min_width_px", 40, 5, 2000)
    max_h = _int_cfg(cfg, "hybrid_vector_line_max_height_px", 4, 1, 30)
    max_segments = _int_cfg(cfg, "hybrid_vector_line_max_segments", 250, 1, 2000)
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT]); y = int(stats[i, cv2.CC_STAT_TOP])
        ww = int(stats[i, cv2.CC_STAT_WIDTH]); hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])
        if ww < min_w or hh > max_h or area < min_w // 3:
            continue
        comp = labels == i
        yy = int(round(np.median(np.where(comp)[0])))
        xs = np.where(np.any(comp[max(0, yy - 1):min(mask.shape[0], yy + 2), :], axis=0))[0]
        if xs.size == 0:
            continue
        x0, x1 = int(xs[0]), int(xs[-1])
        vals = processed_rgb[comp]
        if vals.size:
            rgb = np.median(vals.reshape(-1, 3), axis=0) / 255.0
        else:
            rgb = np.array([0.82, 0.80, 0.90])
        lines.append(VectorLineSegment(x0, yy, x1, yy, max(0.35, float(hh)), (float(rgb[0]), float(rgb[1]), float(rgb[2])), opacity=_float_cfg(cfg, "hybrid_vector_line_opacity", 0.45, 0.05, 1.0)))
        remove |= comp
        if len(lines) >= max_segments:
            break
    if lines and str(cfg.get("hybrid_vector_remove_from_raster", "true")).strip().lower() not in {"0", "false", "no", "off"}:
        mask = mask & (~cv2.dilate(remove.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1).astype(bool))
    return lines, mask


def _has_large_opaque_patch_seam_risk(
    rects: list[tuple[int, int, int, int]],
    img_w: int,
    img_h: int,
    cfg: dict[str, Any],
) -> bool:
    """Detect hybrid patches that are visually safer as a full-page raster.

    This does not change the cleaning algorithm.  It only changes the PDF assembly
    strategy for large opaque rectangular overlays.  A forced TDM diagonal watermark
    ROI can create a huge bottom-right opaque patch; even when the processed pixels
    are correct, the rectangular patch boundary can look like the paper lines or
    color panels were shifted by 1-2 px in some PDF viewers.  For those rare large
    patches, a lossless full-raster page is visually faithful to the processed
    page and eliminates hybrid placement seams.
    """
    if not _bool_cfg(cfg, "hybrid_seam_safe_fallback", False):
        return False

    alpha_mode = str(cfg.get("hybrid_patch_alpha", "hard")).strip().lower()
    if alpha_mode not in {"none", "opaque", "false"}:
        return False

    # The seam issue is tied to the large forced watermark ROI patch used by TDM.
    if not _bool_cfg(cfg, "hybrid_force_watermark_roi_patch", False):
        return False

    img_area = max(1, int(img_w) * int(img_h))
    min_area = _float_cfg(cfg, "hybrid_seam_patch_area_ratio_min", 0.12, 0.0, 1.0)
    min_w = _float_cfg(cfg, "hybrid_seam_patch_width_ratio_min", 0.45, 0.0, 1.0)
    min_h = _float_cfg(cfg, "hybrid_seam_patch_height_ratio_min", 0.25, 0.0, 1.0)

    for x0, y0, x1, y1 in rects:
        rw = max(0, int(x1) - int(x0))
        rh = max(0, int(y1) - int(y0))
        if (rw * rh) / img_area >= min_area and rw / max(1, img_w) >= min_w and rh / max(1, img_h) >= min_h:
            return True
    return False


def build_hybrid_page_result(
    *,
    page_index: int,
    width_pt: float,
    height_pt: float,
    original_rgb: np.ndarray,
    processed_rgb: np.ndarray,
    mode_config: dict[str, Any],
    quality: int,
    output_grayscale: bool,
) -> PageResult:
    h, w = processed_rgb.shape[:2]
    output_mode = str(mode_config.get("output_mode", mode_config.get("pdf_output_mode", "hybrid_auto"))).strip().lower()
    full_fmt = str(mode_config.get("full_raster_format", mode_config.get("output_image_format", "png"))).strip().lower()

    # A hybrid page preserves the original PDF page underneath raster patches.
    # Therefore it cannot guarantee a full grayscale output.  When the user
    # explicitly asks for grayscale, force a full-raster page so every pixel of
    # the resulting PDF is encoded as grayscale.
    if output_grayscale:
        output_mode = "full_raster"

    if output_mode in {"raster", "full_raster", "image", "legacy"}:
        img_bytes = encode_rgb_auto_bytes(processed_rgb, fmt=full_fmt, output_grayscale=output_grayscale, quality=quality, png_compression=_int_cfg(mode_config, 'png_compression', 3, 0, 9))
        return PageResult(page_index, width_pt, height_pt, img_bytes, mode="full_raster", img_w=w, img_h=h)

    mask = build_hybrid_change_mask(original_rgb, processed_rgb, mode_config)
    changed_ratio = float(np.count_nonzero(mask) / max(1, mask.size))
    max_changed_ratio = _float_cfg(mode_config, "hybrid_max_changed_ratio", 0.42, 0.01, 1.0)

    # Optional vector-line extraction happens before patch component creation.
    vector_lines, mask = _horizontal_vector_line_candidates(original_rgb, processed_rgb, mask, mode_config)
    rects = _component_rects(mask, mode_config)
    coverage = float(sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects) / max(1, w * h))
    max_coverage = _float_cfg(mode_config, "hybrid_max_patch_coverage", 0.62, 0.01, 1.0)
    max_patches = _int_cfg(mode_config, "hybrid_max_patches", 120, 1, 2000)

    seam_safe_fallback = _has_large_opaque_patch_seam_risk(rects, w, h, mode_config)

    if output_mode in {"hybrid_auto", "auto"} and (
        changed_ratio > max_changed_ratio
        or coverage > max_coverage
        or len(rects) > max_patches
        or seam_safe_fallback
    ):
        # Too much of the page changed; a full raster page is safer than hundreds of
        # overlapping patches.  V7.7.5 also uses this lossless full-raster assembly
        # for very large opaque TDM watermark patches to avoid PDF viewer subpixel
        # placement seams.  The processed image pixels are unchanged.
        img_bytes = encode_rgb_auto_bytes(processed_rgb, fmt=full_fmt, output_grayscale=output_grayscale, quality=quality, png_compression=_int_cfg(mode_config, 'png_compression', 3, 0, 9))
        return PageResult(page_index, width_pt, height_pt, img_bytes, mode="full_raster", img_w=w, img_h=h, patch_coverage=coverage, changed_pixel_ratio=changed_ratio)

    patches = [_make_patch_png(processed_rgb, mask, r, mode_config) for r in rects]
    return PageResult(
        page_index,
        width_pt,
        height_pt,
        None,
        mode="hybrid",
        img_w=w,
        img_h=h,
        patches=patches,
        vector_lines=vector_lines,
        patch_coverage=coverage,
        changed_pixel_ratio=changed_ratio,
    )


def process_page_with_processor(
    *,
    pdf_path: Path,
    page_index: int,
    processor: Any,
    mode_config: dict[str, Any],
    dpi: int,
    quality: int,
    output_grayscale: bool,
    use_doc_cache: bool = True,
) -> PageResult:
    """Shared page worker body. It does not change image/PDF processing logic."""
    page_rgb: np.ndarray | None = None
    processed_rgb: np.ndarray | None = None
    try:
        page_rgb, width_pt, height_pt = render_page_rgb(pdf_path, page_index, dpi, use_doc_cache=use_doc_cache)
        processed_rgb = processor.process_page(page_rgb)
        processed_rgb = apply_fixed_border_whiteout(
            processed_rgb,
            mode_config.get("header_height", "0%"),
            mode_config.get("footer_height", "0%"),
        )

        # V8.8.5 performance: for modes already forced to full-raster
        # (EBOOK/IPCLASS/grayscale), skip hybrid-diff preparation entirely.
        # This calls the exact same encoder and returns the same PageResult shape
        # as build_hybrid_page_result() would for full-raster output; it only
        # avoids extra branching and keeps the hybrid path untouched for Toán/TDM.
        output_mode = str(mode_config.get("output_mode", mode_config.get("pdf_output_mode", "hybrid_auto"))).strip().lower()
        if output_grayscale or output_mode in {"raster", "full_raster", "image", "legacy"}:
            h, w = processed_rgb.shape[:2]
            full_fmt = str(mode_config.get("full_raster_format", mode_config.get("output_image_format", "png"))).strip().lower()
            img_bytes = encode_rgb_auto_bytes(processed_rgb, fmt=full_fmt, output_grayscale=output_grayscale, quality=quality, png_compression=_int_cfg(mode_config, 'png_compression', 3, 0, 9))
            return PageResult(page_index, width_pt, height_pt, img_bytes, mode="full_raster", img_w=w, img_h=h)

        # Runtime shortcut only: if the processor made no pixel changes, the
        # hybrid assembler would produce a copied source page with zero patches.
        # Returning that PageResult directly avoids diff-mask/morphology work and
        # preserves the same PDF content assembly semantics for hybrid modes.
        if processed_rgb.shape == page_rgb.shape and np.array_equal(processed_rgb, page_rgb):
            h, w = processed_rgb.shape[:2]
            return PageResult(page_index, width_pt, height_pt, None, mode="hybrid", img_w=w, img_h=h)

        return build_hybrid_page_result(
            page_index=page_index,
            width_pt=width_pt,
            height_pt=height_pt,
            original_rgb=page_rgb,
            processed_rgb=processed_rgb,
            mode_config=mode_config,
            quality=quality,
            output_grayscale=output_grayscale,
        )
    except Exception:
        err = traceback.format_exc(limit=8)
        try:
            if page_rgb is None:
                page_rgb, width_pt, height_pt = render_page_rgb(pdf_path, page_index, dpi, use_doc_cache=use_doc_cache)
            fmt = str(mode_config.get("fallback_image_format", "png")).lower()
            img_bytes = encode_rgb_auto_bytes(page_rgb, fmt=fmt, output_grayscale=output_grayscale, quality=quality, png_compression=_int_cfg(mode_config, 'png_compression', 3, 0, 9))
            h, w = page_rgb.shape[:2]
            return PageResult(page_index, width_pt, height_pt, img_bytes, error=err, used_original_fallback=True, mode="full_raster", img_w=w, img_h=h)
        except Exception:
            raise RuntimeError(f"Trang {page_index + 1} lỗi và không tạo được fallback:\n{err}")
    finally:
        del page_rgb, processed_rgb


def process_page_worker(
    pdf_path_str: str,
    page_index: int,
    mode: str,
    mode_config: dict[str, Any],
    dpi: int,
    quality: int,
    output_grayscale: bool,
    use_doc_cache: bool = True,
) -> PageResult:
    """Render, clean, and encode one PDF page. Safe for child processes."""
    processor = get_cached_processor(mode, mode_config)
    return process_page_with_processor(
        pdf_path=Path(pdf_path_str),
        page_index=page_index,
        processor=processor,
        mode_config=mode_config,
        dpi=dpi,
        quality=quality,
        output_grayscale=output_grayscale,
        use_doc_cache=use_doc_cache,
    )


def process_page_worker_from_context(page_index: int) -> PageResult:
    """Process one page using immutable settings installed by worker_init."""
    if not _WORKER_CONTEXT:
        raise RuntimeError("Worker context chưa được khởi tạo.")
    return process_page_with_processor(
        pdf_path=Path(str(_WORKER_CONTEXT["pdf_path"])),
        page_index=int(page_index),
        processor=get_context_processor(),
        mode_config=_WORKER_CONTEXT["mode_config"],
        dpi=int(_WORKER_CONTEXT["dpi"]),
        quality=int(_WORKER_CONTEXT["quality"]),
        output_grayscale=bool(_WORKER_CONTEXT["output_grayscale"]),
        use_doc_cache=True,
    )
