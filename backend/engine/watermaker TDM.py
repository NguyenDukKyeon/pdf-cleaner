# -*- coding: utf-8 -*-
"""
V7.7.6 Colored-panel Watermark Cleanup TDM Cleaner for scanned TDM/TaiLieuOnThi PDFs.

Main goal:
- Remove the diagonal TaiLieuOnThi watermark even when it crosses white interiors of tables
  and answer boxes.
- Preserve real content: math text, table borders, dotted answer-box borders, circles,
  and colored labels.
- Preserve pale colored backgrounds (cyan/pink answer columns and pale answer boxes) by
  reconstructing them from the same local/component background instead of forcing white.

Install:
    pip install pymupdf opencv-python numpy

Usage:
    python clean_document_background_v6_line_restore.py "input.pdf" -o "output_clean.pdf"

Debug:
    python clean_document_background_v6_line_restore.py "input.pdf" -o "output.pdf" --save-pages pages --debug debug
"""

from __future__ import annotations

import argparse
import gc
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping
import tempfile
import pickle
import concurrent.futures
import subprocess
import os
from pathlib import Path

import cv2
cv2.setNumThreads(1)
import fitz  # PyMuPDF
import numpy as np
from PIL import Image
from numpy.lib.stride_tricks import sliding_window_view

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# Geometry for the common TaiLieuOnThi/TDM watermark pattern.
DEFAULT_TOP_BOX = (0.36, 0.000, 0.635, 0.028)  # V6.7 tight: only central watermark, avoids blue header rule
DEFAULT_BOTTOM_BOX = (0.34, 0.962, 0.72, 0.998)  # V6.7 tight: avoid footer blue rule/text as much as possible
DEFAULT_DIAG_P1 = (0.615, 0.965)   # lower-left side of the diagonal watermark band
DEFAULT_DIAG_P2 = (0.965, 0.665)   # upper-right side of the diagonal watermark band


def imwrite_unicode(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower() or ".png"
    params: list[int] = []
    if ext in {".jpg", ".jpeg"}:
        params = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
    elif ext == ".png":
        params = [int(cv2.IMWRITE_PNG_COMPRESSION), 3]
    ok, encoded = cv2.imencode(ext, image, params)
    if not ok:
        raise RuntimeError(f"Cannot encode image as {ext}: {path}")
    encoded.tofile(str(path))


def odd_kernel(value: int, minimum: int = 3, maximum: int | None = None) -> int:
    value = max(minimum, int(value))
    if maximum is not None:
        value = min(value, maximum)
    if value % 2 == 0:
        value += 1
    return value


def render_page_to_bgr(page: fitz.Page, dpi: int) -> np.ndarray:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    del pix, rgb
    return bgr


def ratio_box_to_pixels(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    x1 = max(0, min(width, int(round(box[0] * width))))
    y1 = max(0, min(height, int(round(box[1] * height))))
    x2 = max(0, min(width, int(round(box[2] * width))))
    y2 = max(0, min(height, int(round(box[3] * height))))
    return x1, y1, x2, y2


def build_diagonal_band_mask(
    height: int,
    width: int,
    p1_ratio: tuple[float, float],
    p2_ratio: tuple[float, float],
    band_width_px: int,
    x_min_ratio: float,
    x_max_ratio: float,
    y_min_ratio: float,
    y_max_ratio: float,
) -> np.ndarray:
    """
    Speed optimized diagonal corridor builder.

    Older versions allocated full-page xx/yy float grids.  At 300 DPI that can
    create multiple 70+ MB temporary arrays per page.  This version computes only
    the clipped diagonal ROI and writes it into a uint8 page mask.
    """
    mask = np.zeros((height, width), dtype=np.uint8)

    x_min = max(0, int(x_min_ratio * width))
    x_max = min(width - 1, int(x_max_ratio * width))
    y_min = max(0, int(y_min_ratio * height))
    y_max = min(height - 1, int(y_max_ratio * height))
    if x_max <= x_min or y_max <= y_min:
        return mask

    p1x = float(p1_ratio[0] * width)
    p1y = float(p1_ratio[1] * height)
    p2x = float(p2_ratio[0] * width)
    p2y = float(p2_ratio[1] * height)

    pad = int(max(4, band_width_px + 8))
    rx1 = max(x_min, int(np.floor(min(p1x, p2x) - pad)))
    rx2 = min(x_max, int(np.ceil(max(p1x, p2x) + pad)))
    ry1 = max(y_min, int(np.floor(min(p1y, p2y) - pad)))
    ry2 = min(y_max, int(np.ceil(max(p1y, p2y) + pad)))
    if rx2 <= rx1 or ry2 <= ry1:
        return mask

    yy, xx = np.mgrid[ry1:ry2 + 1, rx1:rx2 + 1]
    xx = xx.astype(np.float32, copy=False)
    yy = yy.astype(np.float32, copy=False)

    vx = p2x - p1x
    vy = p2y - p1y
    denom = max(vx * vx + vy * vy, 1.0)

    t = ((xx - p1x) * vx + (yy - p1y) * vy) / denom
    np.clip(t, 0.0, 1.0, out=t)
    proj_x = p1x + t * vx
    proj_y = p1y + t * vy

    dist2 = (xx - proj_x) * (xx - proj_x) + (yy - proj_y) * (yy - proj_y)
    local = dist2 <= float(band_width_px * band_width_px)
    mask[ry1:ry2 + 1, rx1:rx2 + 1][local] = 255
    return mask



def build_antialiased_text_protect_mask(
    gray: np.ndarray,
    bgr: np.ndarray,
    args: argparse.Namespace,
) -> np.ndarray:
    """
    V7.1 quality guard: protect anti-aliased text/formula strokes that are
    lighter than the strict dark-text threshold but still have high local
    contrast.  This is safer for subscripts, superscripts, minus signs, dotted
    answer boxes and thin table strokes.

    The guard deliberately requires local contrast, so pale paper texture and
    low-contrast watermark haze are not broadly protected.
    """
    h, w = gray.shape[:2]
    local_kernel = odd_kernel(int(getattr(args, "safe_text_aa_local_kernel", 31)), minimum=15, maximum=61)
    local_bg = cv2.medianBlur(gray, local_kernel)
    contrast = local_bg.astype(np.int16) - gray.astype(np.int16)

    gray_max = int(getattr(args, "safe_text_aa_gray_max", 145))
    min_contrast = int(getattr(args, "safe_text_aa_min_contrast", 42))
    strict_gray = int(getattr(args, "safe_text_gray_max", 105))

    # Include strict dark strokes, plus lighter anti-aliased strokes only when
    # they stand out from their local background.
    candidate = (
        (gray <= strict_gray)
        | ((gray <= gray_max) & (contrast >= min_contrast))
    )

    cand_u8 = (candidate.astype(np.uint8) * 255)
    # Bridge tiny antialias gaps without growing into background cells.
    cand_u8 = cv2.morphologyEx(
        cand_u8,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        iterations=1,
    )

    min_area = int(getattr(args, "safe_text_aa_component_min_area", 2))
    max_area = int(getattr(args, "safe_text_aa_component_max_area", 25000))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand_u8, connectivity=8)
    valid = np.zeros(n, dtype=bool)
    if n > 1:
        areas = stats[:, cv2.CC_STAT_AREA]
        widths = stats[:, cv2.CC_STAT_WIDTH]
        heights = stats[:, cv2.CC_STAT_HEIGHT]
        valid = (areas >= min_area) & (areas <= max_area)
        # Reject huge filled regions/long background strips; true text and
        # table strokes stay as compact components or thin lines.
        valid &= ~((widths > int(0.72 * w)) & (heights > int(0.035 * h)))
        valid &= ~((heights > int(0.18 * h)) & (widths > int(0.18 * w)))
        valid[0] = False
    out = (valid[labels].astype(np.uint8) * 255)

    dilate_px = int(getattr(args, "safe_text_aa_dilate", 1))
    if dilate_px > 0:
        k = 2 * dilate_px + 1
        out = cv2.dilate(out, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)), iterations=1)
    return out


def build_content_protect_mask_v5(bgr: np.ndarray, args: argparse.Namespace) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Protect only real strokes/ink, never whole table/answer-box areas.

    Protected:
    - dark math/text strokes;
    - colored ink such as blue/red option labels, D/S, headers;
    - table borders, dotted answer boxes, answer circles, graph lines.

    Not protected:
    - white interiors of tables;
    - pale cyan/pink backgrounds of answer columns;
    - pale answer-box fills.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]

    # Core text/formula shield. Keep this conservative enough so gray watermark core is not fully shielded.
    dark_text = gray <= int(args.safe_text_gray_max)

    # V7.1 quality guard: protect anti-aliased edges and tiny math strokes
    # that are lighter than the strict dark threshold but high-contrast locally.
    if bool(getattr(args, "safe_text_aa_enable", True)):
        text_aa_protect = build_antialiased_text_protect_mask(gray, bgr, args) > 0
    else:
        text_aa_protect = np.zeros_like(gray, dtype=bool)

    # Colored ink shield. Avoid pale backgrounds by requiring either darker value/gray or strong saturation.
    colored_ink = (
        (sat >= int(args.safe_color_ink_sat_min))
        & ((gray <= int(args.safe_color_ink_gray_max)) | (val <= int(args.safe_color_ink_value_max)))
    )

    # Edge-based content shield: table borders, dotted boxes, answer circles, graph axes.
    try:
        edges = cv2.Canny(gray, int(args.edge_canny_low), int(args.edge_canny_high)) > 0
    except Exception:
        edges = np.zeros_like(gray, dtype=bool)
    edge_content = edges & ((gray <= int(args.edge_dark_gray_max)) | ((sat >= int(args.edge_protect_sat_min)) & (gray <= int(args.edge_color_gray_max))))

    edge_u8 = (edge_content.astype(np.uint8) * 255)
    h, w = gray.shape[:2]
    # Long line extraction reinforces table strokes while keeping interiors editable.
    h_kernel_w = max(31, int(w * 0.012))
    v_kernel_h = max(31, int(h * 0.008))
    horizontal = cv2.morphologyEx(
        edge_u8,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel_w, 1)),
        iterations=1,
    )
    vertical = cv2.morphologyEx(
        edge_u8,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_kernel_h)),
        iterations=1,
    )
    line_mask = cv2.bitwise_or(horizontal, vertical) > 0

    protect_bool = dark_text | text_aa_protect | colored_ink | edge_content | line_mask
    protect = (protect_bool.astype(np.uint8) * 255)

    if args.safe_protect_dilate > 0:
        k = 2 * int(args.safe_protect_dilate) + 1
        protect = cv2.dilate(protect, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)), iterations=1)

    # V6.7/V6.8: protect real top-right header text from every later cleanup/restoration mask.
    header_right_protect = build_header_right_protect_mask(bgr, args)
    protect = cv2.bitwise_or(protect, header_right_protect)

    debug = {
        "header_right_protect": header_right_protect,
        "dark_text_protect": (dark_text.astype(np.uint8) * 255),
        "colored_ink_protect": (colored_ink.astype(np.uint8) * 255),
        "text_aa_protect": (text_aa_protect.astype(np.uint8) * 255),
        "edge_content_protect": (edge_content.astype(np.uint8) * 255),
        "line_protect": (line_mask.astype(np.uint8) * 255),
    }
    return protect, debug


def estimate_local_background_bgr(bgr: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    """Robust local background estimate. Median blur removes thin text/watermark better than mean blur."""
    k = odd_kernel(args.local_bg_kernel, minimum=15, maximum=151)
    return cv2.medianBlur(bgr, k)


def build_pale_colored_background_mask(bgr: np.ndarray, protect: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    """
    Detect pale colored fills (cyan/pink columns, pale blue answer boxes) separately.
    These regions should be repaired using their own color, not snapped to white.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    mx = np.max(bgr, axis=2)
    mn = np.min(bgr, axis=2)
    spread = mx.astype(np.int16) - mn.astype(np.int16)

    pale_color = (
        (protect == 0)
        & (gray >= int(args.pale_color_gray_min))
        & (val >= int(args.pale_color_value_min))
        & (sat >= int(args.pale_color_sat_min))
        & (sat <= int(args.pale_color_sat_max))
        & (spread >= int(args.pale_color_spread_min))
    )
    # Smooth tiny holes but do not aggressively grow into white cells.
    mask = (pale_color.astype(np.uint8) * 255)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=1)
    return mask


def snap_or_component_background(
    bgr: np.ndarray,
    local_bg: np.ndarray,
    replace: np.ndarray,
    protect: np.ndarray,
    band: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Build the target image for replacement.

    - Neutral white/paper areas -> pure white or local white.
    - Pale colored fills -> median color from the same connected fill component.
    - Other safe areas -> local median background.
    """
    target = local_bg.copy()

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]

    local_gray = cv2.cvtColor(local_bg, cv2.COLOR_BGR2GRAY)
    local_hsv = cv2.cvtColor(local_bg, cv2.COLOR_BGR2HSV)
    local_sat = local_hsv[:, :, 1]
    mx = np.max(local_bg, axis=2)
    mn = np.min(local_bg, axis=2)
    spread = mx.astype(np.int16) - mn.astype(np.int16)

    # V5 default repair policy: in the safe diagonal corridor, non-content pixels are
    # usually paper/table interiors, so default replacement should be clean white.
    # Pale colored components are restored later and override this white default.
    if args.force_neutral_white:
        target[replace > 0] = (255, 255, 255)

    # Pure white reconstruction for actual paper/white table interiors outside the current
    # replacement mask is kept for debug/target consistency.
    white_local = (
        (local_gray >= int(args.white_local_gray_min))
        & (local_sat <= int(args.white_local_sat_max))
        & (spread <= int(args.white_local_spread_max))
    )
    if args.force_neutral_white:
        target[white_local & (replace > 0)] = (255, 255, 255)

    pale_color_mask = build_pale_colored_background_mask(bgr, protect, args)
    # Restrict component processing around diagonal area to speed up and avoid global oddities.
    around = cv2.dilate(band, cv2.getStructuringElement(cv2.MORPH_RECT, (41, 41)), iterations=1)
    cc_mask = cv2.bitwise_and(pale_color_mask, around)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(cc_mask, connectivity=8)
    min_area = int(args.pale_color_component_min_area)
    max_area = int(args.pale_color_component_max_area)
    replacement_bool = replace > 0

    for lab in range(1, n):
        area = int(stats[lab, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        comp = labels == lab
        comp_replace = comp & replacement_bool
        if not np.any(comp_replace):
            continue

        # Use clean pixels in the same component as the color prototype.
        clean_sample = comp & (protect == 0) & (~replacement_bool)
        if np.count_nonzero(clean_sample) < int(args.pale_color_min_clean_pixels):
            # Fallback: sample from a component dilation, excluding protected/replaced pixels.
            sample_dil = cv2.dilate(
                comp.astype(np.uint8) * 255,
                cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
                iterations=1,
            ) > 0
            clean_sample = sample_dil & (protect == 0) & (~replacement_bool) & (pale_color_mask > 0)
        if np.count_nonzero(clean_sample) < int(args.pale_color_min_clean_pixels):
            # Last resort for cells completely covered by the diagonal mask: use the component
            # itself. The median of a pale fill component is still much closer to the true cell
            # color than white or a contaminated local median.
            clean_sample = comp & (protect == 0)
        if np.count_nonzero(clean_sample) < max(5, int(args.pale_color_min_clean_pixels) // 5):
            continue

        color = np.median(bgr[clean_sample], axis=0)
        # Avoid filling colored components with almost-white fallback.
        if np.max(color) - np.min(color) >= int(args.pale_color_spread_min):
            # V6.9.5 fix: do not grow a pale-color component outside its original
            # geometry here.  Older versions dilated the component inside the whole
            # diagonal band, which could create a stray cyan horizontal line under a
            # colored panel.  True panel repair is handled later by
            # preserve_color_panels_v69(), using exact original panel geometry.
            comp_replace = comp & replacement_bool & (protect == 0) & (band > 0)
            if np.any(comp_replace):
                target[comp_replace] = np.clip(color, 0, 255).astype(np.uint8)

    debug = {
        "pale_colored_bg": pale_color_mask,
        "white_local_bg": (white_local.astype(np.uint8) * 255),
    }
    return target, debug



def build_header_right_protect_mask(bgr: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    """
    V6.7: Preserve real gray header text at the top-right, e.g.
    facebook.com/nguyendang.thai.58 or Tuduymo.com.

    The mask is stroke-based, not a filled rectangle, so the surrounding paper
    remains editable while the letters and anti-aliased edges are protected.
    """
    h, w = bgr.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    if not bool(getattr(args, "header_right_protect", True)):
        return mask

    x1 = int(round(float(args.header_right_protect_x_min) * w))
    x2 = int(round(float(args.header_right_protect_x_max) * w))
    y1 = int(round(float(args.header_right_protect_y_min) * h))
    y2 = int(round(float(args.header_right_protect_y_max) * h))
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return mask

    roi = bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    spread = np.max(roi, axis=2).astype(np.int16) - np.min(roi, axis=2).astype(np.int16)

    text = (
        (gray <= int(args.header_right_protect_gray_max))
        & (
            (gray <= int(args.header_right_protect_gray_strict))
            | (sat >= int(args.header_right_protect_sat_min))
            | (spread >= int(args.header_right_protect_spread_min))
        )
    )
    text_u8 = text.astype(np.uint8) * 255
    kx = max(1, int(args.header_right_protect_dilate_x))
    ky = max(1, int(args.header_right_protect_dilate_y))
    text_u8 = cv2.dilate(text_u8, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)), iterations=1)
    mask[y1:y2, x1:x2] = text_u8
    return mask




def detect_color_panel_geometry_v69(
    original_bgr: np.ndarray,
    protect: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, list[dict[str, object]], dict[str, np.ndarray]]:
    """
    V6.9 helper: detect pale cyan/pink colored panels from the original page.

    Unlike V6.8, the detected panel geometry is not rebuilt by default.  It is
    first used as a guard so the broad diagonal corridor never force-whitens
    colored panels.  Only true watermark candidates inside panels are patched;
    full geometry rebuild is reserved for rare broken-panel fallback cases.
    """
    h, w = original_bgr.shape[:2]
    gray = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    spread = np.max(original_bgr, axis=2).astype(np.int16) - np.min(original_bgr, axis=2).astype(np.int16)

    cyan_like = (
        (hue >= int(args.color_panel_hue_min))
        & (hue <= int(args.color_panel_hue_max))
        & (sat >= int(args.color_panel_sat_min))
        & (sat <= int(args.color_panel_sat_max))
        & (val >= int(args.color_panel_value_min))
        & (gray >= int(args.color_panel_gray_min))
        & (spread >= int(args.color_panel_spread_min))
    )
    pale_colored = (
        (sat >= int(args.color_panel_extra_sat_min))
        & (sat <= int(args.color_panel_extra_sat_max))
        & (val >= int(args.color_panel_value_min))
        & (gray >= int(args.color_panel_gray_min))
        & (spread >= int(args.color_panel_spread_min))
    )

    raw = ((cyan_like | pale_colored).astype(np.uint8) * 255)
    kx = max(3, int(args.color_panel_close_x))
    ky = max(3, int(args.color_panel_close_y))
    raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)), iterations=1)
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)), iterations=1)

    if protect is None:
        protect, _ = build_content_protect_mask_v5(original_bgr, args)
    # Performance: when a caller already passes the page-level protect mask,
    # reuse it instead of recomputing HSV/Canny/morphology for the same page.

    edges = cv2.Canny(gray, int(args.color_panel_edge_canny_low), int(args.color_panel_edge_canny_high)) > 0
    selective_edges = edges & (
        (gray <= int(args.color_panel_edge_dark_gray_max))
        | ((sat >= int(args.color_panel_edge_color_sat_min)) & (gray <= int(args.color_panel_edge_color_gray_max)))
    )
    selective_edges_u8 = (selective_edges.astype(np.uint8) * 255)
    selective_edges_u8 = cv2.dilate(selective_edges_u8, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
    stroke_guard = cv2.bitwise_or(protect, selective_edges_u8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(raw, 8)
    panel_mask = np.zeros((h, w), dtype=np.uint8)
    components: list[dict[str, object]] = []

    min_w = max(int(args.color_panel_min_width_px), int(float(args.color_panel_min_width_ratio) * w))
    min_h = int(args.color_panel_min_height_px)
    max_h = max(min_h + 1, int(float(args.color_panel_max_height_ratio) * h))

    for i in range(1, n):
        x, y, ww, hh, area = [int(v) for v in stats[i]]
        if ww < min_w or hh < min_h or hh > max_h:
            continue
        if area < int(args.color_panel_min_area):
            continue
        bbox_area = max(1, ww * hh)
        if (area / bbox_area) < float(args.color_panel_min_coverage):
            continue
        if (ww / max(1, hh)) < float(args.color_panel_min_aspect):
            continue

        ex = int(args.color_panel_expand_x)
        ey = int(args.color_panel_expand_y)
        x1 = max(0, x - ex)
        x2 = min(w, x + ww + ex)
        y1 = max(0, y - ey)
        y2 = min(h, y + hh + ey)

        component = labels == i
        sample = component & (stroke_guard == 0) & ((cyan_like | pale_colored))
        if np.count_nonzero(sample) < int(args.color_panel_min_sample_pixels):
            sample = (raw > 0) & (stroke_guard == 0)
            tmp = np.zeros_like(sample, dtype=bool)
            tmp[y1:y2, x1:x2] = True
            sample = sample & tmp
        if np.count_nonzero(sample) < int(args.color_panel_min_sample_pixels):
            continue
        color = np.median(original_bgr[sample], axis=0).astype(np.float32)
        if (float(np.max(color) - np.min(color)) < float(args.color_panel_min_color_spread)):
            continue

        panel_mask[y1:y2, x1:x2] = 255
        components.append({
            "bbox": (x1, y1, x2, y2),
            "color": color,
            "label": i,
        })

    debug = {
        "color_panel_raw": raw,
        "color_panel_mask": panel_mask,
        "color_panel_stroke_guard": stroke_guard,
    }
    return panel_mask, components, debug
def guided_header_footer_cleanup(bgr: np.ndarray, args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray]:
    """Clean known central top/bottom watermark strips with conservative protection."""
    out = bgr.copy()
    h, w = out.shape[:2]
    debug = np.zeros((h, w), dtype=np.uint8)

    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    header_right_global_protect = build_header_right_protect_mask(out, args)

    for box in (DEFAULT_TOP_BOX, DEFAULT_BOTTOM_BOX):
        x1, y1, x2, y2 = ratio_box_to_pixels(box, w, h)
        if x2 <= x1 or y2 <= y1:
            continue
        gp = gray[y1:y2, x1:x2]
        sp = sat[y1:y2, x1:x2]
        patch = out[y1:y2, x1:x2]

        protect = ((gp < args.header_footer_protect_gray) | (sp > args.header_footer_protect_sat)).astype(np.uint8) * 255
        protect = cv2.dilate(protect, np.ones((3, 3), dtype=np.uint8), iterations=1) > 0
        # V6.7: with tight ROI this normally should not overlap the
        # top-right facebook header. Keep those strokes intact as a safety guard.
        protect = protect | (header_right_global_protect[y1:y2, x1:x2] > 0)
        replace = (
            (gp >= args.header_footer_gray_low)
            & (gp <= args.header_footer_gray_high)
            & (sp <= args.header_footer_sat_max)
            & (~protect)
        )
        if np.any(replace):
            patch[replace] = (255, 255, 255)
            out[y1:y2, x1:x2] = patch
            debug_patch = debug[y1:y2, x1:x2]
            debug_patch[replace] = 255
            debug[y1:y2, x1:x2] = debug_patch

    return out, debug


def smart_diagonal_cleanup_v5(bgr: np.ndarray, args: argparse.Namespace) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    out = bgr.copy()
    h, w = out.shape[:2]

    band_width = args.diag_band_width_px
    if band_width <= 0:
        # Wide enough to cover tilted TaiLieuOnThi letters at 300 DPI, still limited by safe corridor.
        band_width = max(105, int(round(175 * (w / 2480.0))))

    band = build_diagonal_band_mask(
        h,
        w,
        DEFAULT_DIAG_P1,
        DEFAULT_DIAG_P2,
        band_width,
        args.diag_x_min,
        args.diag_x_max,
        args.diag_y_min,
        args.diag_y_max,
    )

    protect, protect_debug = build_content_protect_mask_v5(out, args)

    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]

    # V7 balanced mode: skip expensive color-panel geometry detection when
    # no pale/cyan panel intersects the diagonal watermark corridor.
    color_panel_mask_for_diag = np.zeros((h, w), dtype=np.uint8)
    color_panel_diag_debug: dict[str, np.ndarray] = {}
    color_panel_gate = True
    if bool(getattr(args, "v7_conditional_color_panel", False)):
        color_panel_gate = page_has_color_panel_candidate_in_band(out, band, args)
    setattr(args, "_v7_last_color_panel_needed", bool(color_panel_gate))
    if (
        bool(getattr(args, "color_panel_preserve", True))
        and bool(getattr(args, "color_panel_protect_from_corridor", True))
        and color_panel_gate
    ):
        color_panel_mask_for_diag, _, color_panel_diag_debug = detect_color_panel_geometry_v69(out, protect, args)

    local_bg = estimate_local_background_bgr(out, args)
    bg_gray = cv2.cvtColor(local_bg, cv2.COLOR_BGR2GRAY)
    bg_hsv = cv2.cvtColor(local_bg, cv2.COLOR_BGR2HSV)
    bg_sat = bg_hsv[:, :, 1]
    diff = bg_gray.astype(np.int16) - gray.astype(np.int16)

    # Thresholded watermark candidates: residual gray/low-sat strokes in the diagonal band.
    candidate = (
        (band > 0)
        & (protect == 0)
        & (gray >= int(args.diag_gray_low))
        & (gray <= int(args.diag_gray_high))
        & (sat <= int(args.diag_sat_max))
        & (diff >= int(args.diag_min_bg_diff))
    )

    # Aggressive-but-safe corridor reconstruction for background pixels in the band.
    # This removes leftover watermark inside white table/answer-box interiors.
    corridor = (
        (band > 0)
        & (protect == 0)
        & (gray >= int(args.corridor_gray_low))
        & (gray <= int(args.corridor_gray_high))
        & (sat <= int(args.corridor_sat_max))
        & (bg_gray >= int(args.corridor_bg_gray_min))
    )

    # Extra halo for barely visible watermark on paper.
    halo = (
        (band > 0)
        & (protect == 0)
        & (gray >= int(args.diag_halo_gray_low))
        & (gray <= int(args.diag_halo_gray_high))
        & (sat <= int(args.diag_halo_sat_max))
        & (bg_gray >= int(args.diag_halo_bg_gray_min))
        & (bg_sat <= int(args.diag_halo_bg_sat_max))
        & (diff >= int(args.diag_halo_min_bg_diff))
    )

    panel_bool = color_panel_mask_for_diag > 0
    # V6.9 key change: corridor repair is disabled inside colored panels to avoid
    # cyan boxes being whitened.  Candidate/halo pixels are still allowed because
    # they are darker than their local background and represent actual watermark ink.
    replace_bool = candidate | halo | (corridor & (~panel_bool))
    panel_allowed_bool = (candidate | halo) & panel_bool
    replace = (replace_bool.astype(np.uint8) * 255)

    if args.diag_close_kernel > 1:
        k = odd_kernel(args.diag_close_kernel, minimum=3, maximum=21)
        replace = cv2.morphologyEx(replace, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)), iterations=1)
    if args.diag_dilate > 0:
        replace = cv2.dilate(replace, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=int(args.diag_dilate))

    # Keep replacement inside the safe diagonal corridor and never touch real strokes.
    replace = cv2.bitwise_and(replace, band)
    replace = cv2.subtract(replace, protect)
    if np.any(panel_bool):
        # After morphology, again prevent broad corridor/morphology from leaking into
        # panel backgrounds.  Add back only true watermark candidates/halos.
        panel_allowed = (panel_allowed_bool.astype(np.uint8) * 255)
        replace = cv2.bitwise_or(cv2.bitwise_and(replace, cv2.bitwise_not(color_panel_mask_for_diag)), panel_allowed)
        replace = cv2.bitwise_and(replace, band)
        replace = cv2.subtract(replace, protect)

    # Build background target with specific handling for white paper vs pale colored fills.
    target_bg, bg_debug = snap_or_component_background(out, local_bg, replace, protect, band, args)

    if np.any(replace):
        mask = replace > 0
        if args.use_inpaint_before_background:
            out = cv2.inpaint(out, replace, float(args.inpaint_radius), cv2.INPAINT_TELEA)
        alpha = float(args.replace_blend_alpha)
        if alpha >= 0.999:
            out[mask] = target_bg[mask]
        else:
            out[mask] = np.clip(
                alpha * target_bg[mask].astype(np.float32) + (1.0 - alpha) * out[mask].astype(np.float32),
                0,
                255,
            ).astype(np.uint8)

    debug = {
        "diag_band": band,
        "diag_replace": replace,
        "safe_protect": protect,
        "local_bg_gray": bg_gray,
        **protect_debug,
        **bg_debug,
        **{("diag_" + k): v for k, v in color_panel_diag_debug.items()},
    }
    return out, debug



def median_filter1d_v6(values: np.ndarray, kernel_size: int) -> np.ndarray:
    """Small dependency-free median filter for 1-D row profiles."""
    k = odd_kernel(kernel_size, minimum=3, maximum=301)
    pad = k // 2
    padded = np.pad(values.astype(np.float32), (pad, pad), mode="edge")
    return np.median(sliding_window_view(padded, k), axis=1)


def detect_ruled_line_centers_v6(bgr: np.ndarray, args: argparse.Namespace) -> tuple[list[int], dict[str, np.ndarray]]:
    """
    V6.1: detect a periodic ruled-line grid and extrapolate it down to the bottom
    restoration zone.  V6 only returned centers inside the detection ROI; therefore
    the last ruled lines near the footer could be erased by diagonal cleanup but not
    redrawn.  This version uses the stable period/phase from the main body, then
    extends centers through line_restore_y_max.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    x1 = int(round(args.line_detect_x_min * w))
    x2 = int(round(args.line_detect_x_max * w))
    y1 = int(round(args.line_detect_y_min * h))
    y2 = int(round(args.line_detect_y_max * h))
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    if x2 <= x1 or y2 <= y1 or (y2 - y1) < 100:
        return [], {"ruled_line_score": np.zeros((h, 64), dtype=np.uint8)}

    roi = gray[y1:y2, x1:x2].copy()
    # Hide dark content so fractions/text do not dominate the row profile.
    roi[roi < int(args.line_detect_dark_exclude_gray)] = 255
    row_mean = roi.mean(axis=1).astype(np.float32)
    baseline = median_filter1d_v6(row_mean, int(args.line_row_baseline_kernel))
    score = np.clip(baseline - row_mean, 0, None)
    score = median_filter1d_v6(score, 3)

    if args.line_spacing_min_px > 0 and args.line_spacing_max_px > 0:
        s_min = int(args.line_spacing_min_px)
        s_max = int(args.line_spacing_max_px)
    else:
        s_min = max(28, int(round(h * 0.018)))
        s_max = min(100, int(round(h * 0.042)))
    if s_max <= s_min:
        s_max = s_min + 10

    best: tuple[float, int, int] | None = None
    for spacing in range(s_min, s_max + 1):
        for phase in range(spacing):
            total = 0.0
            count = 0
            for yr in range(phase, len(score), spacing):
                lo = max(0, yr - int(args.line_phase_search_radius))
                hi = min(len(score), yr + int(args.line_phase_search_radius) + 1)
                if hi <= lo:
                    continue
                total += float(score[lo:hi].max())
                count += 1
            if count == 0:
                continue
            val = total / count
            if best is None or val > best[0]:
                best = (val, spacing, phase)

    profile_img = np.ones((h, 160), dtype=np.uint8) * 255
    if len(score) > 0:
        scale = 255.0 / max(float(score.max()), 1e-6)
        for idx, val in enumerate(score):
            y = y1 + idx
            if 0 <= y < h:
                length = int(min(159, max(0, val * scale * 0.65)))
                profile_img[y, :length] = 0

    if best is None or best[0] < float(args.line_min_periodic_score):
        return [], {"ruled_line_score": profile_img}

    _, spacing, phase = best
    raw_centers: list[int] = []
    # First refine centers inside the reliable detection ROI.
    for yr in range(phase, len(score), spacing):
        lo = max(0, yr - int(args.line_center_refine_radius))
        hi = min(len(score), yr + int(args.line_center_refine_radius) + 1)
        if hi <= lo:
            continue
        loc = lo + int(np.argmax(score[lo:hi]))
        y = y1 + loc
        raw_centers.append(int(y))

    # Deduplicate refined centers.
    refined: list[int] = []
    min_gap = max(4, spacing // 3)
    for y in sorted(raw_centers):
        if not refined or y - refined[-1] > min_gap:
            refined.append(y)

    if len(refined) < int(args.line_min_centers):
        return [], {"ruled_line_score": profile_img}

    # V6.1 fix: extrapolate the same grid beyond the detection ROI so the final ruled
    # lines above the footer are restored too.  Use the median offset of refined centers
    # from the theoretical grid to keep the phase aligned.
    offsets = []
    for y in refined:
        k = round((y - (y1 + phase)) / spacing)
        pred = y1 + phase + k * spacing
        offsets.append(y - pred)
    offset = int(round(float(np.median(offsets)))) if offsets else 0

    y_min_restore = int(round(args.line_restore_y_min * h))
    y_max_restore = int(round(args.line_restore_y_max * h))
    centers: list[int] = []
    # Generate a little beyond the requested range, then clip.
    k_start = int(np.floor((y_min_restore - (y1 + phase + offset)) / spacing)) - 2
    k_end = int(np.ceil((y_max_restore - (y1 + phase + offset)) / spacing)) + 2
    for k in range(k_start, k_end + 1):
        y_pred = int(round(y1 + phase + offset + k * spacing))
        if y_min_restore <= y_pred <= y_max_restore:
            # If inside detection ROI, replace with nearest refined center when close.
            near = min(refined, key=lambda yy: abs(yy - y_pred))
            if abs(near - y_pred) <= max(3, spacing // 6):
                centers.append(int(near))
            else:
                centers.append(y_pred)

    centers = sorted(set(centers))
    if len(centers) < int(args.line_min_centers):
        return [], {"ruled_line_score": profile_img}

    centers_img = np.zeros((h, w), dtype=np.uint8)
    for y in centers:
        if 0 <= y < h:
            centers_img[y, int(0.05 * w):int(0.95 * w)] = 255

    return centers, {
        "ruled_line_score": profile_img,
        "ruled_line_centers": centers_img,
    }


def detect_footer_border_y_v64(bgr: np.ndarray, args: argparse.Namespace) -> int:
    """
    Locate the strong horizontal footer separator line.  It is much more continuous
    and more saturated than normal notebook ruled lines, so row coverage is a stable
    detector even after cleaning.
    """
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    spread = np.max(bgr, axis=2).astype(np.int16) - np.min(bgr, axis=2).astype(np.int16)

    x1 = int(round(float(args.footer_detect_x_min) * w))
    x2 = int(round(float(args.footer_detect_x_max) * w))
    y1 = int(round(float(args.footer_detect_y_min) * h))
    y2 = int(round(float(args.footer_detect_y_max) * h))
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return int(round(float(args.line_restore_y_max) * h))

    # Strong blue/green footer rule: continuous row, moderate saturation/spread, not dark text.
    rule = (
        (gray >= int(args.footer_rule_gray_min))
        & (gray <= int(args.footer_rule_gray_max))
        & (sat >= int(args.footer_rule_sat_min))
        & (spread >= int(args.footer_rule_spread_min))
    )
    counts = rule[y1:y2, x1:x2].sum(axis=1)
    min_cov = int(round(float(args.footer_rule_min_coverage) * (x2 - x1)))
    if len(counts) and int(counts.max()) >= min_cov:
        # The line is usually 2-4 px thick. Use the first row of the strongest continuous run.
        cand = np.where(counts >= min_cov)[0] + y1
        groups: list[list[int]] = []
        for y in cand.tolist():
            if not groups or y - groups[-1][-1] > 1:
                groups.append([y])
            else:
                groups[-1].append(y)
        # Choose the lowest strong group, because header/bottom objects can sometimes be detected.
        g = groups[-1]
        return int(g[0])

    # Fallback: look for a long horizontal edge near the bottom.
    edges = cv2.Canny(gray, 30, 110)
    opened = cv2.morphologyEx(
        edges,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(80, int(0.08 * w)), 1)),
        iterations=1,
    )
    counts2 = (opened[y1:y2, x1:x2] > 0).sum(axis=1)
    min_cov2 = int(round(0.25 * (x2 - x1)))
    if len(counts2) and int(counts2.max()) >= min_cov2:
        cand = np.where(counts2 >= min_cov2)[0] + y1
        return int(cand[-1])

    return int(round(float(args.line_restore_y_max) * h))


def extend_centers_to_footer_v64(centers: list[int], footer_y: int, args: argparse.Namespace) -> list[int]:
    if not centers:
        return centers
    centers = sorted(set(int(y) for y in centers))
    if len(centers) >= 3:
        spacing = int(round(float(np.median(np.diff(centers)))))
    else:
        spacing = int(args.footer_template_spacing_px) if int(args.footer_template_spacing_px) > 0 else 60
    spacing = max(8, spacing)
    y_limit = int(footer_y - int(args.footer_guard_px))
    # Add one or more expected notebook lines that were beyond the old y_max.
    y = centers[-1] + spacing
    while y <= y_limit:
        centers.append(int(y))
        y += spacing
    return sorted(set(centers))



def build_ruled_line_evidence_v71(
    bgr: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Conservative evidence map for real pale ruled-paper lines.
    It rejects dark text/table borders and only keeps pixels slightly darker
    than their local vertical paper neighborhood.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    spread = np.max(bgr, axis=2).astype(np.int16) - np.min(bgr, axis=2).astype(np.int16)

    paper_like = (
        (gray >= int(args.line_paper_gray_min))
        & (gray <= int(args.line_paper_gray_max))
        & (sat <= int(args.line_paper_sat_max))
        & (spread <= int(args.line_paper_spread_max))
    )

    k = odd_kernel(int(getattr(args, "line_evidence_baseline_kernel", 21)), minimum=7, maximum=61)
    baseline = cv2.blur(gray, (1, k)).astype(np.int16)
    contrast = baseline - gray.astype(np.int16)
    evidence = paper_like & (contrast >= int(getattr(args, "line_evidence_min_contrast", 2)))

    evidence_u8 = cv2.morphologyEx(
        evidence.astype(np.uint8) * 255,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(getattr(args, "line_evidence_close_x", 5))), 1)),
        iterations=1,
    )
    return evidence_u8 > 0, paper_like


def filter_ruled_centers_by_evidence_v71(
    centers: list[int],
    original_bgr: np.ndarray,
    x_allowed: np.ndarray,
    args: argparse.Namespace,
) -> tuple[list[int], dict[str, np.ndarray]]:
    """
    Keep only centers that have enough real ruled-line evidence in the original page.
    This prevents table rows, text baselines, and watermark remnants from being
    mistaken as a notebook-line grid.
    """
    h, w = original_bgr.shape[:2]
    if not centers:
        return [], {"ruled_line_evidence": np.zeros((h, w), dtype=np.uint8)}

    evidence, _ = build_ruled_line_evidence_v71(original_bgr, args)
    evidence_dbg = evidence.astype(np.uint8) * 255

    x_cols = x_allowed.any(axis=0)
    x_width = int(np.count_nonzero(x_cols))
    if x_width <= 0:
        return [], {"ruled_line_evidence": evidence_dbg}

    min_cov = float(getattr(args, "line_evidence_min_row_coverage", 0.18))
    min_px = int(max(8, round(min_cov * x_width)))
    radius = int(getattr(args, "line_evidence_y_radius", 1))

    kept: list[int] = []
    kept_img = np.zeros((h, w), dtype=np.uint8)
    for y in centers:
        if y < 0 or y >= h:
            continue
        lo = max(0, y - radius)
        hi = min(h, y + radius + 1)
        counts = [int(np.count_nonzero(evidence[yy] & x_cols)) for yy in range(lo, hi)]
        if counts and max(counts) >= min_px:
            kept.append(int(y))
            kept_img[max(0, y - 1):min(h, y + 2), x_cols] = 255

    if len(kept) < int(getattr(args, "line_min_centers", 8)):
        kept = []
        kept_img[:] = 0

    return kept, {
        "ruled_line_evidence": evidence_dbg,
        "ruled_line_evidence_kept_centers": kept_img,
    }


def build_line_restore_damage_gate_v71(
    diag_replace: np.ndarray | None,
    band: np.ndarray | None,
    shape: tuple[int, int],
    args: argparse.Namespace,
) -> np.ndarray:
    """
    Redraw ruled lines only where watermark cleanup actually touched pixels.
    This prevents non-logical global line painting across blank areas/tables.
    """
    h, w = shape
    if not bool(getattr(args, "line_restore_damaged_only", True)):
        return np.ones((h, w), dtype=bool)

    if diag_replace is None or np.count_nonzero(diag_replace) == 0:
        return np.zeros((h, w), dtype=bool)

    scale = max(0.45, min(1.8, float(getattr(args, "dpi", 300)) / 300.0))
    kx = max(3, int(round(float(getattr(args, "line_restore_damage_gate_dilate_x", 72)) * scale)))
    ky = max(1, int(round(float(getattr(args, "line_restore_damage_gate_dilate_y", 3)) * scale)))
    gate = cv2.dilate(
        (diag_replace > 0).astype(np.uint8) * 255,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)),
        iterations=1,
    )

    if bool(getattr(args, "line_restore_limit_to_diag_band", True)) and band is not None and np.count_nonzero(band) > 0:
        band_gate = cv2.dilate(
            (band > 0).astype(np.uint8) * 255,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, kx // 3), max(1, ky))),
            iterations=1,
        )
        gate = cv2.bitwise_and(gate, band_gate)

    return gate > 0


def build_ruled_line_gap_bridge_gate_v7601(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    centers: list[int],
    x_allowed: np.ndarray,
    protect: np.ndarray | None,
    restore_gate: np.ndarray,
    band: np.ndarray | None,
    footer_y: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    V7.6.1: bridge broken ruled-paper lines after watermark cleanup.

    The old damaged-only gate follows sparse diag_replace pixels.  On very pale
    notebook lines, cleanup can erase a long horizontal segment while diag_replace
    remains dotted/sparse, so the line is redrawn as short dashes.  This gate
    closes the real ruled-line evidence along each detected line row, but only
    inside blank paper + diagonal/damage zone and never over protected text,
    graphs, panels, or the footer.
    """
    h, w = cleaned_bgr.shape[:2]
    gate = np.zeros((h, w), dtype=bool)
    debug: dict[str, np.ndarray] = {"ruled_line_gap_bridge_gate": np.zeros((h, w), dtype=np.uint8)}

    if not bool(getattr(args, "line_gap_bridge", True)):
        return gate, debug
    if not centers or restore_gate is None or not np.any(restore_gate):
        return gate, debug

    evidence, _paper_like_original = build_ruled_line_evidence_v71(original_bgr, args)

    gray_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    spread_c = np.max(cleaned_bgr, axis=2).astype(np.int16) - np.min(cleaned_bgr, axis=2).astype(np.int16)

    # Blank ruled-paper area after cleanup. This is deliberately looser than the
    # line evidence detector because the missing line pixels have often become white.
    blank_paper = (
        (gray_c >= int(getattr(args, "line_gap_bridge_gray_min", 168)))
        & (sat_c <= int(getattr(args, "line_gap_bridge_sat_max", 135)))
        & (spread_c <= int(getattr(args, "line_gap_bridge_spread_max", 70)))
    )

    # Extra text/shape guard.  Protect already contains strong text/graphs; this
    # catches faint anti-aliased strokes that would be crossed by a rebuilt line.
    text_like = (gray_c < int(getattr(args, "line_gap_bridge_text_gray_max", 178))) & (sat_c < 155)
    text_guard = cv2.dilate(
        text_like.astype(np.uint8) * 255,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(getattr(args, "line_gap_bridge_text_dilate_x", 9))), 3)),
        iterations=1,
    ) > 0
    if protect is not None:
        text_guard |= (protect > 0)

    scale = max(0.45, min(1.8, float(getattr(args, "dpi", 300)) / 300.0))
    close_x = max(25, int(round(float(getattr(args, "line_gap_bridge_close_x", 260)) * scale)))
    damage_x = max(25, int(round(float(getattr(args, "line_gap_bridge_damage_dilate_x", 190)) * scale)))
    y_radius = max(0, int(getattr(args, "line_gap_bridge_y_radius", 1)))
    min_clean_px = max(6, int(getattr(args, "line_gap_bridge_min_clean_pixels", 18)))

    damage_source = restore_gate.astype(np.uint8) * 255
    if band is not None and np.count_nonzero(band) > 0:
        # Only use the diagonal band as an expansion source; the final mask still
        # needs blank-paper + no-text guards, so it will not redraw through content.
        damage_source = cv2.bitwise_or(damage_source, (band > 0).astype(np.uint8) * 255)
    damage_near = cv2.dilate(
        damage_source,
        cv2.getStructuringElement(cv2.MORPH_RECT, (damage_x, max(1, 2 * y_radius + 1))),
        iterations=1,
    ) > 0

    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_x, 1))
    y_limit = max(0, int(footer_y - int(getattr(args, "footer_guard_px", 32))))

    for y in centers:
        if y < 1 or y >= h - 1 or y >= y_limit:
            continue
        lo = max(0, y - y_radius)
        hi = min(h, y + y_radius + 1)

        # Evidence of the real ruled line in the same row neighborhood.
        ev_row = np.zeros(w, dtype=np.uint8)
        x_row = np.zeros(w, dtype=bool)
        for yy in range(lo, hi):
            ev_row |= ((evidence[yy] & x_allowed[yy] & (~text_guard[yy])).astype(np.uint8) * 255)
            x_row |= x_allowed[yy]
        if np.count_nonzero(ev_row) < min_clean_px:
            continue

        # Close horizontal gaps between real line fragments. This connects only
        # rows that already have ruled-line evidence on both sides.
        closed = (cv2.morphologyEx(ev_row.reshape(1, -1), cv2.MORPH_CLOSE, close_kernel, iterations=1).reshape(-1) > 0)
        bridge_row = closed & x_row

        # Keep the gate local to the watermark/damage region and blank paper.
        for yy in range(lo, hi):
            m = (
                bridge_row
                & damage_near[yy]
                & blank_paper[yy]
                & (~text_guard[yy])
                & x_allowed[yy]
            )
            if np.any(m):
                gate[yy, m] = True

    debug["ruled_line_gap_bridge_gate"] = gate.astype(np.uint8) * 255
    return gate, debug


def _remove_tiny_components_v763(mask_u8: np.ndarray, min_area: int) -> np.ndarray:
    """Remove tiny specks from a binary mask without depending on skimage."""
    if mask_u8 is None or mask_u8.size == 0:
        return mask_u8
    min_area = max(1, int(min_area))
    n, labels, stats, _ = cv2.connectedComponentsWithStats((mask_u8 > 0).astype(np.uint8), connectivity=8)
    if n <= 1:
        return (mask_u8 > 0).astype(np.uint8) * 255
    keep = np.zeros(n, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return (keep[labels].astype(np.uint8) * 255)


def build_content_aware_line_blocker_v763(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    protect: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    V7.6.3: content-aware clipping guard for global ruled-line reconstruction.

    The v761 bridge still depended on local fragments/damage evidence, so very pale
    ruled lines could remain dashed in graph/text neighborhoods.  This guard separates
    real content from pale paper using both original and cleaned pages, then lets the
    synthesis layer draw only on blank paper.  It intentionally does not treat pale
    ruled-paper pixels as content, but it blocks text, formulas, table borders, graph
    axes/curves, dotted boxes, colored strokes, and panel fills.
    """
    h, w = cleaned_bgr.shape[:2]
    if protect is None:
        protect = np.zeros((h, w), dtype=np.uint8)

    gray_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2GRAY)
    gray_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2HSV)
    hsv_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    sat_o = hsv_o[:, :, 1]
    spread_c = np.max(cleaned_bgr, axis=2).astype(np.int16) - np.min(cleaned_bgr, axis=2).astype(np.int16)
    spread_o = np.max(original_bgr, axis=2).astype(np.int16) - np.min(original_bgr, axis=2).astype(np.int16)

    # Dark/anti-aliased text and formula strokes.  Use both original and cleaned so
    # strokes protected before cleanup remain protected after local whitening.
    dark_thr = int(getattr(args, "line_global_text_gray_max", 170))
    dark_or_aa = ((gray_c < dark_thr) & (sat_c < 170)) | ((gray_o < dark_thr) & (sat_o < 170))

    # Colored content/graph curves.  The blue/red plotted curves are saturated enough;
    # panel fills are handled by fill_guard below and should not be line-painted.
    ink_sat = int(getattr(args, "line_global_colored_ink_sat_min", 36))
    colored_ink = (
        ((sat_c >= ink_sat) & (gray_c <= int(getattr(args, "line_global_colored_ink_gray_max", 238))))
        | ((sat_o >= ink_sat) & (gray_o <= int(getattr(args, "line_global_colored_ink_gray_max", 238))))
    )

    # Edges catch graph axes, table borders, dashed guide lines and answer boxes.
    low = int(getattr(args, "line_global_edge_canny_low", 24))
    high = int(getattr(args, "line_global_edge_canny_high", 96))
    try:
        edges_c = cv2.Canny(gray_c, low, high) > 0
        edges_o = cv2.Canny(gray_o, low, high) > 0
    except Exception:
        edges_c = np.zeros((h, w), dtype=bool)
        edges_o = np.zeros((h, w), dtype=bool)
    edge_gray_max = int(getattr(args, "line_global_edge_gray_max", 160))
    edge_guard = (
        (edges_c & ((gray_c <= edge_gray_max) | ((sat_c >= ink_sat) & (gray_c <= int(getattr(args, "line_global_colored_ink_gray_max", 238))))))
        | (edges_o & ((gray_o <= edge_gray_max) | ((sat_o >= ink_sat) & (gray_o <= int(getattr(args, "line_global_colored_ink_gray_max", 238))))))
    )

    # Pale colored fills (cyan boxes, DS columns, shaded headers) should not receive
    # notebook rules through their interior, even if they are blank.
    fill_guard = (
        ((gray_c >= int(getattr(args, "line_global_fill_gray_min", 135)))
         & (sat_c >= int(getattr(args, "line_global_fill_sat_min", 12)))
         & (spread_c >= int(getattr(args, "line_global_fill_spread_min", 5))))
        | ((gray_o >= int(getattr(args, "line_global_fill_gray_min", 135)))
           & (sat_o >= int(getattr(args, "line_global_fill_sat_min", 12)))
           & (spread_o >= int(getattr(args, "line_global_fill_spread_min", 5))))
    )
    fill_u8 = cv2.morphologyEx(
        fill_guard.astype(np.uint8) * 255,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (
            max(3, int(getattr(args, "line_global_fill_close_x", 11))),
            max(3, int(getattr(args, "line_global_fill_close_y", 7))),
        )),
        iterations=1,
    )
    fill_guard = fill_u8 > 0

    # Compose and dilate guards.  Use anisotropic dilation: wider in X because ruled
    # lines are horizontal and should stop before/after glyphs/graph strokes.
    base_guard = (protect > 0) | dark_or_aa | colored_ink | edge_guard | fill_guard
    base_u8 = base_guard.astype(np.uint8) * 255
    base_u8 = _remove_tiny_components_v763(base_u8, int(getattr(args, "line_global_guard_min_area", 2)))
    dilate_x = max(1, int(getattr(args, "line_global_guard_dilate_x", 5)))
    dilate_y = max(1, int(getattr(args, "line_global_guard_dilate_y", 3)))
    guard = cv2.dilate(
        base_u8,
        cv2.getStructuringElement(cv2.MORPH_RECT, (dilate_x, dilate_y)),
        iterations=1,
    ) > 0

    return guard, {
        "ruled_line_global_content_guard": guard.astype(np.uint8) * 255,
        "ruled_line_global_fill_guard": fill_guard.astype(np.uint8) * 255,
        "ruled_line_global_edge_guard": edge_guard.astype(np.uint8) * 255,
    }



def estimate_ruled_line_grid_confidence_v765(
    original_bgr: np.ndarray,
    centers: list[int],
    x_allowed: np.ndarray,
    args: argparse.Namespace,
) -> tuple[float, dict[str, np.ndarray]]:
    """
    V7.6.5 safety gate: estimate whether the page really has a stable notebook
    ruled-line grid. Global synthesis is only allowed aggressively when there is
    enough periodic/evidence support; otherwise it falls back to local bridge/damage.
    """
    h, w = original_bgr.shape[:2]
    dbg = np.zeros((h, w), dtype=np.uint8)
    if not centers or len(centers) < int(getattr(args, "line_min_centers", 8)):
        return 0.0, {"ruled_line_global_confidence_map": dbg}

    evidence, _paper = build_ruled_line_evidence_v71(original_bgr, args)
    y_min = max(0, int(round(float(getattr(args, "line_restore_y_min", 0.05)) * h)))
    y_max = min(h, int(round(float(getattr(args, "line_restore_y_max", 0.92)) * h)))
    valid = [int(y) for y in centers if y_min <= int(y) < y_max]
    if len(valid) < int(getattr(args, "line_min_centers", 8)):
        return 0.0, {"ruled_line_global_confidence_map": dbg}

    coverages = []
    for y in valid:
        lo = max(0, y - int(getattr(args, "line_evidence_y_radius", 1)))
        hi = min(h, y + int(getattr(args, "line_evidence_y_radius", 1)) + 1)
        row_ev = np.any(evidence[lo:hi] > 0, axis=0)
        allowed = x_allowed[y] if 0 <= y < h else np.zeros(w, dtype=bool)
        denom = int(np.count_nonzero(allowed))
        cov = float(np.count_nonzero(row_ev & allowed)) / max(1, denom)
        coverages.append(cov)
        if cov > 0:
            dbg[max(0, y - 1):min(h, y + 2), allowed] = int(min(255, max(32, 255 * min(1.0, cov / 0.35))))

    if len(coverages) == 0:
        return 0.0, {"ruled_line_global_confidence_map": dbg}

    evidence_score = float(np.median(coverages)) / max(0.01, float(getattr(args, "line_evidence_min_row_coverage", 0.18)))
    evidence_score = max(0.0, min(1.0, evidence_score))

    if len(valid) >= 4:
        diffs = np.diff(np.array(sorted(valid), dtype=np.float32))
        med = float(np.median(diffs)) if len(diffs) else 0.0
        mad = float(np.median(np.abs(diffs - med))) if len(diffs) else 999.0
        periodic_score = 1.0 - min(1.0, mad / max(2.0, 0.12 * med if med > 0 else 1.0))
    else:
        periodic_score = 0.0

    count_score = min(1.0, len(valid) / max(1.0, float(getattr(args, "line_min_centers", 8)) * 1.5))
    confidence = 0.52 * evidence_score + 0.33 * periodic_score + 0.15 * count_score
    confidence = float(max(0.0, min(1.0, confidence)))
    return confidence, {"ruled_line_global_confidence_map": dbg}

def build_ruled_line_global_reconstruction_gate_v763(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    centers: list[int],
    x_allowed: np.ndarray,
    protect: np.ndarray | None,
    restore_gate: np.ndarray,
    bridge_gate: np.ndarray,
    band: np.ndarray | None,
    footer_y: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    V7.6.6: ROI-only global ruled-line synthesis gate with content-aware clipping and confidence gating.

    Instead of only joining visible fragments, generate a logical ruled-line layer
    from the detected page grid.  The gate is then clipped by blank-paper tests and
    content guards.  This fixes long dashed gaps next to graphs/text where v761 did
    not have enough local evidence to bridge.
    """
    h, w = cleaned_bgr.shape[:2]
    gate = np.zeros((h, w), dtype=bool)
    debug: dict[str, np.ndarray] = {"ruled_line_global_reconstruction_gate": np.zeros((h, w), dtype=np.uint8)}

    if not bool(getattr(args, "line_global_reconstruction", True)):
        return gate, debug
    if not centers:
        return gate, debug

    gray_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    spread_c = np.max(cleaned_bgr, axis=2).astype(np.int16) - np.min(cleaned_bgr, axis=2).astype(np.int16)

    # Blank paper may be pure white after watermark removal, so this threshold is
    # intentionally permissive.  Content blocking is handled by the guard.
    blank_paper = (
        (gray_c >= int(getattr(args, "line_global_blank_gray_min", 162)))
        & (sat_c <= int(getattr(args, "line_global_blank_sat_max", 150)))
        & (spread_c <= int(getattr(args, "line_global_blank_spread_max", 80)))
    )

    content_guard, guard_debug = build_content_aware_line_blocker_v763(original_bgr, cleaned_bgr, protect, args)
    debug.update(guard_debug)

    # V7.6.5: confidence-driven safety. If the ruled-paper grid is weak, do not
    # synthesize across the full page; restrict to damage/bridge neighborhoods or
    # disable global synthesis completely.
    confidence, conf_debug = estimate_ruled_line_grid_confidence_v765(original_bgr, centers, x_allowed, args)
    debug.update(conf_debug)
    debug["ruled_line_global_confidence_value"] = np.ones((16, 256), dtype=np.uint8) * int(round(255 * confidence))
    min_conf = float(getattr(args, "line_global_min_confidence", 0.54))
    aggressive_conf = float(getattr(args, "line_global_aggressive_confidence", 0.70))
    if confidence < min_conf:
        debug["ruled_line_global_reconstruction_gate"] = gate.astype(np.uint8) * 255
        return gate, debug

    # Prefer a broad diagonal/damage neighborhood for the problematic watermark area.
    # Optionally allow full blank-page synthesis for high-quality final exports.
    damage_source = np.zeros((h, w), dtype=np.uint8)
    if restore_gate is not None:
        damage_source = cv2.bitwise_or(damage_source, restore_gate.astype(np.uint8) * 255)
    if bridge_gate is not None:
        damage_source = cv2.bitwise_or(damage_source, bridge_gate.astype(np.uint8) * 255)
    if band is not None and np.count_nonzero(band) > 0:
        damage_source = cv2.bitwise_or(damage_source, (band > 0).astype(np.uint8) * 255)

    scale = max(0.45, min(1.8, float(getattr(args, "dpi", 300)) / 300.0))
    dx = max(25, int(round(float(getattr(args, "line_global_damage_dilate_x", 420)) * scale)))
    dy = max(1, int(round(float(getattr(args, "line_global_damage_dilate_y", 5)) * scale)))
    damage_near = cv2.dilate(
        damage_source,
        cv2.getStructuringElement(cv2.MORPH_RECT, (dx, dy)),
        iterations=1,
    ) > 0

    # Missing-line pixels are usually almost white after cleanup.  This lets the
    # synthesizer fill holes even when damage masks are sparse.
    missing_like = gray_c >= int(getattr(args, "line_global_missing_gray_min", 236))
    missing_like &= sat_c <= int(getattr(args, "line_global_missing_sat_max", 150))
    missing_like &= spread_c <= int(getattr(args, "line_global_missing_spread_max", 85))

    # V7.6.6 hotfix: do NOT add a synthetic line layer over the whole page.
    # Global reconstruction is now used only as a geometry model; drawing is clipped
    # to the watermark/diagonal/damage ROI. This prevents the "add line layer" issue
    # where clean ruled lines outside the watermark became dirtier after hybrid overlay.
    full_page_requested = bool(getattr(args, "line_global_full_page", False))
    scope_mode = str(getattr(args, "line_restore_scope", "watermark_roi_only")).strip().lower()
    allow_full_page = scope_mode in {"full_page", "global_full_page", "legacy_full_page"}
    full_page = allow_full_page and full_page_requested and (confidence >= aggressive_conf)
    soft_scope = damage_near  # watermark/diagonal/damage ROI only
    scope = np.ones((h, w), dtype=bool) if full_page else soft_scope

    y_radius = max(0, int(getattr(args, "line_global_y_radius", 1)))
    y_min = max(0, int(round(float(getattr(args, "line_restore_y_min", 0.05)) * h)))
    y_limit = min(
        h,
        int(round(float(getattr(args, "line_restore_y_max", 0.92)) * h)),
        max(0, int(footer_y - int(getattr(args, "footer_guard_px", 32)))),
    )
    min_run = max(1, int(getattr(args, "line_global_min_run_px", 2)))

    for y in centers:
        if y < y_min or y > y_limit or y < 1 or y >= h - 1:
            continue
        lo = max(0, y - y_radius)
        hi = min(h, y + y_radius + 1)
        for yy in range(lo, hi):
            m = x_allowed[yy] & blank_paper[yy] & scope[yy] & (~content_guard[yy])
            if not np.any(m):
                continue
            # Remove isolated one-pixel blips; keep continuous blank stretches.
            row = m.astype(np.uint8).reshape(1, -1) * 255
            if min_run > 1:
                row = cv2.morphologyEx(
                    row,
                    cv2.MORPH_OPEN,
                    cv2.getStructuringElement(cv2.MORPH_RECT, (min_run, 1)),
                    iterations=1,
                )
            m2 = row.reshape(-1) > 0
            if np.any(m2):
                gate[yy, m2] = True

    debug["ruled_line_global_reconstruction_gate"] = gate.astype(np.uint8) * 255
    debug["ruled_line_global_scope"] = scope.astype(np.uint8) * 255
    debug["ruled_line_global_blank_paper"] = blank_paper.astype(np.uint8) * 255
    return gate, debug


def reconstruct_watermark_roi_background_v767(
    cleaned_bgr: np.ndarray,
    original_bgr: np.ndarray,
    diag_replace: np.ndarray | None,
    protect: np.ndarray | None,
    band: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    V7.6.7: replace (not add over) the damaged diagonal watermark ROI background
    before ruled-line reconstruction.

    Purpose:
    - remove ghost/dirty paper texture inside the diagonal watermark ROI;
    - erase old broken ruled-line fragments only inside that ROI;
    - keep strokes (text, formulas, graph axes/curves, table borders) untouched;
    - then let restore_ruled_lines_v6 redraw only the missing ruled-line segments.
    """
    out = cleaned_bgr.copy()
    h, w = out.shape[:2]
    debug: dict[str, np.ndarray] = {
        "watermark_roi_bg_reconstruct_mask": np.zeros((h, w), dtype=np.uint8),
    }

    if not bool(getattr(args, "watermark_roi_background_reconstruct", True)):
        return out, debug

    if protect is None:
        protect = np.zeros((h, w), dtype=np.uint8)
    if diag_replace is None:
        diag_replace = np.zeros((h, w), dtype=np.uint8)
    if band is None:
        band = np.zeros((h, w), dtype=np.uint8)

    if np.count_nonzero(diag_replace) == 0 and np.count_nonzero(band) == 0:
        return out, debug

    gray_c = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    spread_c = np.max(out, axis=2).astype(np.int16) - np.min(out, axis=2).astype(np.int16)

    # Content guard for background reconstruction must NOT use original-page edges,
    # because the watermark itself exists in the original and would otherwise be
    # protected as "content".  Build a cleaned-page stroke guard instead: protect
    # strong text/formula/graph/table strokes, but allow pale watermark ghosts and
    # old ruled-line fragments to be cleaned.
    content_gray_max = int(getattr(args, "watermark_roi_bg_content_gray_max", 155))
    content_sat_min = int(getattr(args, "watermark_roi_bg_content_sat_min", 42))
    dark_content = gray_c <= content_gray_max
    colored_content = (sat_c >= content_sat_min) & (gray_c <= 238)
    try:
        edges_c = cv2.Canny(gray_c, int(getattr(args, "edge_canny_low", 35)), int(getattr(args, "edge_canny_high", 120))) > 0
    except Exception:
        edges_c = np.zeros((h, w), dtype=bool)
    edge_content = edges_c & ((gray_c <= content_gray_max + 10) | colored_content)
    content_guard = (protect > 0) | dark_content | colored_content | edge_content
    guard_debug: dict[str, np.ndarray] = {}
    extra_dx = max(1, int(getattr(args, "watermark_roi_bg_content_dilate_x", 5)))
    extra_dy = max(1, int(getattr(args, "watermark_roi_bg_content_dilate_y", 3)))
    if extra_dx > 1 or extra_dy > 1:
        content_guard = cv2.dilate(
            content_guard.astype(np.uint8) * 255,
            cv2.getStructuringElement(cv2.MORPH_RECT, (extra_dx, extra_dy)),
            iterations=1,
        ) > 0

    # Light/paper-like pixels that are safe to reconstruct.  Keep this generous:
    # the content guard blocks real strokes, while this mask catches residual ghost
    # texture and broken pale ruled-line fragments.
    paperish = (
        (gray_c >= int(getattr(args, "watermark_roi_bg_gray_min", 176)))
        & (sat_c <= int(getattr(args, "watermark_roi_bg_sat_max", 160)))
        & (spread_c <= int(getattr(args, "watermark_roi_bg_spread_max", 95)))
    )

    scale = max(0.45, min(1.8, float(getattr(args, "dpi", 300)) / 300.0))
    dx = max(3, int(round(float(getattr(args, "watermark_roi_bg_expand_x", 26)) * scale)))
    dy = max(1, int(round(float(getattr(args, "watermark_roi_bg_expand_y", 7)) * scale)))

    seed = (diag_replace > 0).astype(np.uint8) * 255
    if np.count_nonzero(seed) > 0:
        seed = cv2.dilate(seed, cv2.getStructuringElement(cv2.MORPH_RECT, (dx, dy)), iterations=1)
    else:
        # Extremely defensive fallback: use only the diagonal band, but require
        # paper-like pixels and a later line-row intersection. This avoids broad
        # cleaning on pages where diag detection failed.
        seed = np.zeros((h, w), dtype=np.uint8)

    band_bool = band > 0
    seed_bool = seed > 0

    # Clean non-content paper in the watermark ROI.  In v767 the default is the
    # whole diagonal watermark band, not just sparse diag_replace seeds, because
    # very pale watermark ghosts often survive below the diff threshold.
    if bool(getattr(args, "watermark_roi_bg_full_band", True)):
        roi_base = band_bool
    else:
        roi_base = seed_bool & band_bool
    bg_roi = roi_base & paperish & (~content_guard)

    # Also erase old broken ruled-line pixels within the same watermark ROI.  This
    # prevents the earlier v765/v766 "add line layer" look: we first clean the old
    # damaged line/background, then the line restore pass draws a fresh segment.
    line_roi = np.zeros((h, w), dtype=bool)
    try:
        footer_y = detect_footer_border_y_v64(out, args)
        centers, _dbg_centers = detect_ruled_line_centers_v6(original_bgr, args)
        centers = extend_centers_to_footer_v64(centers, footer_y, args)
        yr = max(0, int(getattr(args, "watermark_roi_bg_line_erase_y_radius", 1)))
        y_min = max(0, int(round(float(getattr(args, "line_restore_y_min", 0.05)) * h)))
        y_max = min(h, int(round(float(getattr(args, "line_restore_y_max", 0.92)) * h)))
        for y in centers:
            if y < y_min or y >= y_max:
                continue
            lo = max(0, int(y) - yr)
            hi = min(h, int(y) + yr + 1)
            line_roi[lo:hi, :] |= (roi_base[lo:hi, :] & paperish[lo:hi, :] & (~content_guard[lo:hi, :]))
    except Exception:
        pass

    roi = bg_roi | line_roi
    roi_u8 = roi.astype(np.uint8) * 255
    if np.count_nonzero(roi_u8) == 0:
        debug.update({
            "watermark_roi_bg_safe_background": paperish.astype(np.uint8) * 255,
            "watermark_roi_bg_content_guard": content_guard.astype(np.uint8) * 255,
        })
        return out, debug

    # Close tiny holes inside the ROI, but keep it inside the diagonal band and out
    # of content strokes.  This creates a clean, replaceable background patch.
    roi_u8 = cv2.morphologyEx(
        roi_u8,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, dx // 2), max(3, dy))),
        iterations=1,
    )
    roi = (roi_u8 > 0) & band_bool & paperish & (~content_guard)
    roi_u8 = roi.astype(np.uint8) * 255

    local_bg = estimate_local_background_bgr(out, args)
    target_bg, bg_debug = snap_or_component_background(out, local_bg, roi_u8, protect, band, args)

    if bool(getattr(args, "watermark_roi_bg_use_inpaint", False)):
        try:
            out = cv2.inpaint(out, roi_u8, float(getattr(args, "inpaint_radius", 2.0)), cv2.INPAINT_TELEA)
        except Exception:
            pass

    alpha = float(getattr(args, "watermark_roi_bg_alpha", 1.0))
    alpha = max(0.0, min(1.0, alpha))
    if alpha >= 0.999:
        out[roi] = target_bg[roi]
    else:
        out[roi] = np.clip(
            alpha * target_bg[roi].astype(np.float32) + (1.0 - alpha) * out[roi].astype(np.float32),
            0,
            255,
        ).astype(np.uint8)

    debug.update({
        "watermark_roi_bg_reconstruct_mask": roi_u8,
        "watermark_roi_bg_safe_background": paperish.astype(np.uint8) * 255,
        "watermark_roi_bg_content_guard": content_guard.astype(np.uint8) * 255,
        "watermark_roi_bg_seed": seed.astype(np.uint8),
    })
    # Keep the most useful existing background debug masks if requested.
    if bool(getattr(args, "_need_debug", False)):
        for k, v in bg_debug.items():
            debug["watermark_roi_bg_" + k] = v
        for k, v in guard_debug.items():
            debug["watermark_roi_bg_" + k] = v
    return out, debug


def restore_ruled_lines_v6(
    cleaned_bgr: np.ndarray,
    original_bgr: np.ndarray,
    diag_replace: np.ndarray,
    protect: np.ndarray,
    band: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    V6.4 footer-safe ruled-line template rebuild.

    V6.3 redrew the ruled grid globally, but the last lines above the footer could
    still stay broken because the drawing gate was too dependent on current pixel
    classification.  V6.4 treats the bottom ruled-paper zone as a separate layer:
    it detects the footer separator, extrapolates the ruled-line period from the
    stable body, then redraws the expected bottom lines up to - but never into -
    the footer.
    """
    out = cleaned_bgr.copy()
    h, w = out.shape[:2]
    debug: dict[str, np.ndarray] = {}

    if protect is None:
        protect = np.zeros((h, w), dtype=np.uint8)

    footer_y = detect_footer_border_y_v64(out, args)
    centers, line_debug = detect_ruled_line_centers_v6(original_bgr, args)
    centers = extend_centers_to_footer_v64(centers, footer_y, args)
    debug.update(line_debug)

    centers_img = np.zeros((h, w), dtype=np.uint8)
    for yy in centers:
        if 0 <= yy < h:
            centers_img[yy, int(0.05 * w):int(0.95 * w)] = 255
    debug["ruled_line_centers_v64"] = centers_img

    restore_mask = np.zeros((h, w), dtype=np.uint8)
    footer_mask = np.zeros((h, w), dtype=np.uint8)
    if 0 <= footer_y < h:
        footer_mask[max(0, footer_y - 1):min(h, footer_y + 2), :] = 255
    debug["footer_border_detected"] = footer_mask

    if not centers:
        debug["ruled_line_restore_mask"] = restore_mask
        return out, debug

    gray_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    hsv_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
    sat_o = hsv_o[:, :, 1]
    spread_o = np.max(original_bgr, axis=2).astype(np.int16) - np.min(original_bgr, axis=2).astype(np.int16)

    gray_c = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    spread_c = np.max(out, axis=2).astype(np.int16) - np.min(out, axis=2).astype(np.int16)

    if diag_replace is not None:
        damage = cv2.dilate(
            diag_replace,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(args.line_damage_dilate_x)), max(1, int(args.line_damage_dilate_y)))),
            iterations=1,
        )
    else:
        damage = np.zeros((h, w), dtype=np.uint8)
    if band is not None:
        band_pad = cv2.dilate(band, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5)), iterations=1)
    else:
        band_pad = np.zeros((h, w), dtype=np.uint8)

    # Global draw layer, as in V6.3, kept conservative for the whole page.
    paper_like_draw = (
        (gray_c >= max(150, int(args.line_paper_gray_min) - 25))
        & (gray_c <= 255)
        & (sat_c <= max(130, int(args.line_paper_sat_max) + 35))
        & (spread_c <= max(55, int(args.line_paper_spread_max) + 25))
    )
    paper_like_sample = (
        (gray_o >= int(args.line_paper_gray_min))
        & (gray_o <= int(args.line_paper_gray_max))
        & (sat_o <= int(args.line_paper_sat_max))
        & (spread_o <= int(args.line_paper_spread_max))
    )

    colored_fill = (
        (gray_c >= 130)
        & (sat_c >= 16)
        & (spread_c >= 7)
        & (protect == 0)
    )
    colored_fill = cv2.morphologyEx(
        (colored_fill.astype(np.uint8) * 255),
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
        iterations=1,
    ) > 0

    # Strong edge/text guard, excluding faint ruled lines themselves.
    edges = cv2.Canny(gray_c, 35, 120)
    edge_guard = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1) > 0

    x_allowed = np.zeros((h, w), dtype=bool)
    x1 = int(round(args.line_restore_x_min * w))
    x2 = int(round(args.line_restore_x_max * w))
    x_allowed[:, max(0, x1):min(w, x2)] = True
    y1 = int(round(args.line_restore_y_min * h))
    # Dynamic end: just above footer, not a fixed ratio.
    y2 = min(int(round(args.line_restore_y_max * h)), int(footer_y - int(args.footer_guard_px)))
    if bool(args.footer_template_extend_to_border):
        y2 = int(footer_y - int(args.footer_guard_px))

    # V7.2: validate the page really has ruled-paper lines, then limit repair
    # to pixels affected by watermark cleanup.
    centers, evidence_debug = filter_ruled_centers_by_evidence_v71(centers, original_bgr, x_allowed, args)
    debug.update(evidence_debug)
    restore_gate = build_line_restore_damage_gate_v71(diag_replace, band, (h, w), args)
    debug["ruled_line_damage_only_gate"] = restore_gate.astype(np.uint8) * 255

    bridge_gate, bridge_debug = build_ruled_line_gap_bridge_gate_v7601(
        original_bgr=original_bgr,
        cleaned_bgr=out,
        centers=centers,
        x_allowed=x_allowed,
        protect=protect,
        restore_gate=restore_gate,
        band=band,
        footer_y=footer_y,
        args=args,
    )
    debug.update(bridge_debug)

    global_gate, global_debug = build_ruled_line_global_reconstruction_gate_v763(
        original_bgr=original_bgr,
        cleaned_bgr=out,
        centers=centers,
        x_allowed=x_allowed,
        protect=protect,
        restore_gate=restore_gate,
        bridge_gate=bridge_gate,
        band=band,
        footer_y=footer_y,
        args=args,
    )
    debug.update(global_debug)

    effective_restore_gate = restore_gate | bridge_gate | global_gate
    debug["ruled_line_effective_restore_gate"] = effective_restore_gate.astype(np.uint8) * 255

    if not centers or not np.any(effective_restore_gate):
        debug["ruled_line_restore_mask"] = restore_mask
        return out, debug

    default_color = np.array(args.line_fallback_bgr, dtype=np.uint8)

    # Make a stable line color from many clean line candidates in the body.
    global_samples = []
    for y in centers:
        if y < y1 or y >= min(h, footer_y - int(args.footer_guard_px)):
            continue
        if y > footer_y - int(args.footer_template_zone_px):
            # bottom zone can be contaminated; use upper/body rows for color prototype
            continue
        for yy in range(max(0, y - 1), min(h, y + 2)):
            m = (
                x_allowed[yy]
                & paper_like_sample[yy]
                & (protect[yy] == 0)
                & (band_pad[yy] == 0)
                & (damage[yy] == 0)
            )
            if np.count_nonzero(m) > int(args.line_min_sample_pixels):
                vals = original_bgr[yy, m]
                # V7.7.4 performance: gray_o is the same cvtColor(original_bgr)
                # result for the whole page, so reuse it instead of converting
                # a small BGR slice inside the row loop.
                lum = gray_o[yy, m]
                q = np.percentile(lum, float(args.line_low_percentile))
                pick = vals[lum <= q + 4]
                if len(pick) > 0:
                    global_samples.append(pick)
    if global_samples:
        line_color_global = np.median(np.concatenate(global_samples, axis=0), axis=0).astype(np.uint8)
    else:
        line_color_global = default_color.copy()
    mean_col = int(np.mean(line_color_global))
    if mean_col < 205 or mean_col > int(args.line_too_white_gray):
        line_color_global = default_color.copy()

    # Debug bottom zone mask.
    footer_zone_dbg = np.zeros((h, w), dtype=np.uint8)
    footer_zone_top = max(0, int(footer_y - int(args.footer_template_zone_px)))
    footer_zone_dbg[footer_zone_top:max(0, int(footer_y - int(args.footer_guard_px))), max(0, x1):min(w, x2)] = 255
    debug["footer_template_zone"] = footer_zone_dbg

    for y in centers:
        if y < y1 or y > y2 or y < 1 or y >= h - 1:
            continue

        bottom_zone = y >= footer_y - int(args.footer_template_zone_px)
        if bottom_zone:
            color = line_color_global.copy()
            alphas = (
                (0, float(args.footer_line_alpha_center)),
                (-1, float(args.footer_line_alpha_edge)),
                (1, float(args.footer_line_alpha_edge)),
            )
        else:
            # Regular V6.3 row-wise color sampling.
            sample_rows = []
            for yy in range(max(0, y - 1), min(h, y + 2)):
                sample_mask = (
                    x_allowed[yy]
                    & paper_like_sample[yy]
                    & (protect[yy] == 0)
                    & (band_pad[yy] == 0)
                    & (damage[yy] == 0)
                )
                if np.count_nonzero(sample_mask) > 0:
                    vals = original_bgr[yy, sample_mask]
                    # V7.7.4 performance: reuse page-level grayscale instead of
                    # calling cv2.cvtColor for each sampled row.
                    lum = gray_o[yy, sample_mask]
                    q = np.percentile(lum, float(args.line_low_percentile))
                    pick = vals[lum <= q + 4]
                    if len(pick) > 0:
                        sample_rows.append(pick)
            if sample_rows:
                color = np.median(np.concatenate(sample_rows, axis=0), axis=0).astype(np.uint8)
            else:
                color = line_color_global.copy()
            mean_col = int(np.mean(color))
            if mean_col < 210 or mean_col > int(args.line_too_white_gray):
                color = line_color_global.copy()
            alphas = ((0, float(args.line_alpha_center)), (-1, float(args.line_alpha_edge)), (1, float(args.line_alpha_edge)))

        for dy, alpha in alphas:
            yy = y + dy
            if yy < 0 or yy >= h:
                continue
            if yy >= footer_y - int(args.footer_guard_px):
                continue

            # V7.6.5: split synthetic global pixels from legacy damage/bridge pixels.
            # global_gate already embeds the blank-paper + stroke-level content guard, so
            # do not block it again with broad colored_fill/edge guards; those broad gates
            # were the main cause of dashed ruled lines after v763.
            gline = global_gate[yy]
            legacy_gate = effective_restore_gate[yy] & (~gline)
            edge_ok_legacy = ~edge_guard[yy]
            m_global = x_allowed[yy] & gline
            if bottom_zone:
                m_legacy = (
                    x_allowed[yy]
                    & (gray_c[yy] >= int(args.footer_line_draw_gray_min))
                    & (sat_c[yy] <= int(args.footer_line_draw_sat_max))
                    & (spread_c[yy] <= int(args.footer_line_draw_spread_max))
                    & (~colored_fill[yy])
                    & (protect[yy] == 0)
                    & edge_ok_legacy
                    & legacy_gate
                )
            else:
                m_legacy = (
                    x_allowed[yy]
                    & paper_like_draw[yy]
                    & (~colored_fill[yy])
                    & (protect[yy] == 0)
                    & edge_ok_legacy
                    & legacy_gate
                )
            m = m_global | m_legacy
            if not np.any(m):
                continue
            out[yy, m] = np.clip(
                alpha * color.astype(np.float32) + (1.0 - alpha) * out[yy, m].astype(np.float32),
                0,
                255,
            ).astype(np.uint8)
            restore_mask[yy, m] = 255

    debug["ruled_line_restore_mask"] = restore_mask
    debug["ruled_line_damage_zone"] = damage
    debug["ruled_line_colored_fill_guard"] = (colored_fill.astype(np.uint8) * 255)
    return out, debug



def _colored_panel_watermark_cleanup_component_v776(
    original_bgr: np.ndarray,
    out: np.ndarray,
    rect: tuple[int, int, int, int],
    color: np.ndarray,
    protect: np.ndarray,
    stroke_guard: np.ndarray,
    diag_band: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, np.ndarray]:
    """
    V7.7.6: remove faint diagonal watermark remnants on flat colored panels.

    This is intentionally a placement/cleanup side-branch, not a change to the
    main watermark algorithm.  It only runs on detected pale colored panel
    geometry, only in the diagonal watermark band, and only when the component is
    flat enough to be a panel fill instead of a real image/illustration.
    """
    h, w = out.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in rect]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return out, np.zeros((h, w), dtype=np.uint8)

    crop_o = original_bgr[y1:y2, x1:x2]
    crop_c = out[y1:y2, x1:x2]
    prot = protect[y1:y2, x1:x2] > 0
    stroke = stroke_guard[y1:y2, x1:x2] > 0
    band = diag_band[y1:y2, x1:x2] > 0
    if crop_o.size == 0 or np.count_nonzero(band) < int(getattr(args, "colored_panel_wm_min_band_pixels", 8)):
        return out, np.zeros((h, w), dtype=np.uint8)

    gray_o = cv2.cvtColor(crop_o, cv2.COLOR_BGR2GRAY)
    gray_c = cv2.cvtColor(crop_c, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(crop_c, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    spread_c = np.max(crop_c, axis=2).astype(np.int16) - np.min(crop_c, axis=2).astype(np.int16)

    # Image/illustration guard: the non-watermark panel background must be flat.
    # Use original pixels outside the diagonal band and outside real strokes.
    flat_sample = (~band) & (~prot) & (~stroke) & (gray_o >= int(getattr(args, "color_panel_rebuild_gray_min", 120)))
    if np.count_nonzero(flat_sample) < int(getattr(args, "colored_panel_wm_min_flat_sample", 180)):
        flat_sample = (~prot) & (~stroke) & (gray_o >= int(getattr(args, "color_panel_rebuild_gray_min", 120)))
    if np.count_nonzero(flat_sample) < int(getattr(args, "colored_panel_wm_min_flat_sample", 180)):
        return out, np.zeros((h, w), dtype=np.uint8)

    sample = crop_o[flat_sample].astype(np.float32)
    color_f = np.asarray(color, dtype=np.float32).reshape(1, 3)
    std_max = float(np.max(np.std(sample, axis=0)))
    p95_delta = float(np.percentile(np.max(np.abs(sample - color_f), axis=1), 95))
    if std_max > float(getattr(args, "colored_panel_wm_flat_std_max", 22.0)):
        return out, np.zeros((h, w), dtype=np.uint8)
    if p95_delta > float(getattr(args, "colored_panel_wm_flat_delta_p95_max", 46.0)):
        return out, np.zeros((h, w), dtype=np.uint8)

    color_u8 = np.clip(color_f.reshape(3), 0, 255).astype(np.uint8)
    color_gray = int(cv2.cvtColor(color_u8.reshape(1, 1, 3), cv2.COLOR_BGR2GRAY)[0, 0])

    # Strong content guard: keep real formula/text/borders.  This avoids touching
    # actual math in the cyan box while still allowing pale watermark ink through.
    real_text_gray = int(getattr(args, "colored_panel_wm_real_text_gray_max", 132))
    aa_edge_gray = int(getattr(args, "colored_panel_wm_real_edge_gray_max", 166))
    hard_text = gray_o <= real_text_gray
    try:
        edges_o = cv2.Canny(gray_o, int(getattr(args, "colored_panel_wm_edge_canny_low", 28)), int(getattr(args, "colored_panel_wm_edge_canny_high", 105))) > 0
    except Exception:
        edges_o = np.zeros_like(gray_o, dtype=bool)
    hard_edge = edges_o & (gray_o <= aa_edge_gray)
    hard_content = hard_text | hard_edge | prot
    guard_x = max(1, int(getattr(args, "colored_panel_wm_content_guard_dilate_x", 3)))
    guard_y = max(1, int(getattr(args, "colored_panel_wm_content_guard_dilate_y", 2)))
    if np.count_nonzero(hard_content):
        hard_content = cv2.dilate(hard_content.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_RECT, (guard_x, guard_y)), iterations=1) > 0

    cur_delta = np.max(np.abs(crop_c.astype(np.int16) - color_u8.reshape(1, 1, 3).astype(np.int16)), axis=2)
    orig_delta = np.max(np.abs(crop_o.astype(np.int16) - color_u8.reshape(1, 1, 3).astype(np.int16)), axis=2)
    min_delta = int(getattr(args, "colored_panel_wm_min_delta", 9))
    min_darkening = int(getattr(args, "colored_panel_wm_min_darkening", 3))

    panel_bg_like = (
        band
        & (~hard_content)
        & (gray_o >= int(getattr(args, "colored_panel_wm_original_gray_min", 136)))
        & (gray_c >= int(getattr(args, "colored_panel_wm_gray_min", 136)))
        & (gray_c <= int(getattr(args, "colored_panel_wm_gray_max", 252)))
        & (sat_c <= int(getattr(args, "colored_panel_wm_sat_max", 135)))
        & (spread_c <= int(getattr(args, "colored_panel_wm_spread_max", 105)))
    )
    residual = panel_bg_like & (
        (cur_delta >= min_delta)
        | (orig_delta >= min_delta)
        | (gray_c <= max(0, color_gray - min_darkening))
    )

    if np.count_nonzero(residual) < int(getattr(args, "colored_panel_wm_min_pixels", 6)):
        return out, np.zeros((h, w), dtype=np.uint8)

    # Close/dilate only inside eligible panel background to catch anti-aliased
    # edges of diagonal watermark letters.  Re-apply all content guards afterward.
    close_x = max(1, int(getattr(args, "colored_panel_wm_close_x", 5)))
    close_y = max(1, int(getattr(args, "colored_panel_wm_close_y", 3)))
    dil_x = max(1, int(getattr(args, "colored_panel_wm_dilate_x", 5)))
    dil_y = max(1, int(getattr(args, "colored_panel_wm_dilate_y", 3)))
    m_u8 = residual.astype(np.uint8) * 255
    if close_x > 1 or close_y > 1:
        m_u8 = cv2.morphologyEx(m_u8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (close_x, close_y)), iterations=1)
    if dil_x > 1 or dil_y > 1:
        m_u8 = cv2.dilate(m_u8, cv2.getStructuringElement(cv2.MORPH_RECT, (dil_x, dil_y)), iterations=1)
    m = (m_u8 > 0) & panel_bg_like
    if np.count_nonzero(m) < int(getattr(args, "colored_panel_wm_min_pixels", 6)):
        return out, np.zeros((h, w), dtype=np.uint8)

    alpha = float(getattr(args, "colored_panel_wm_alpha", 1.0))
    alpha = max(0.0, min(1.0, alpha))
    if alpha >= 0.999:
        crop_c[m] = color_u8
    else:
        crop_c[m] = np.clip(alpha * color_u8.reshape(1, 3).astype(np.float32) + (1.0 - alpha) * crop_c[m].astype(np.float32), 0, 255).astype(np.uint8)
    out[y1:y2, x1:x2] = crop_c

    full_mask = np.zeros((h, w), dtype=np.uint8)
    full_mask[y1:y2, x1:x2][m] = 255
    return out, full_mask


def colored_panel_watermark_cleanup_v776(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    protect: np.ndarray | None,
    diag_band: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Run the V7.7.6 flat colored-panel watermark cleanup as a standalone pass."""
    out = cleaned_bgr.copy()
    h, w = out.shape[:2]
    debug: dict[str, np.ndarray] = {}
    if not bool(getattr(args, "colored_panel_watermark_cleanup", False)):
        return out, debug
    if diag_band is None or np.count_nonzero(diag_band) == 0:
        return out, debug
    if protect is None:
        protect, _ = build_content_protect_mask_v5(original_bgr, args)

    panel_mask, components, base_debug = detect_color_panel_geometry_v69(original_bgr, protect, args)
    if not components or np.count_nonzero(panel_mask) == 0:
        return out, debug
    stroke_guard = base_debug.get("color_panel_stroke_guard", protect)
    total = np.zeros((h, w), dtype=np.uint8)
    preview = out.copy() if bool(getattr(args, "_need_debug", False)) else None
    for comp in components:
        x1, y1, x2, y2 = comp["bbox"]  # type: ignore[index]
        color = np.asarray(comp["color"], dtype=np.float32)  # type: ignore[index]
        out, wm_mask = _colored_panel_watermark_cleanup_component_v776(
            original_bgr=original_bgr,
            out=out,
            rect=(int(x1), int(y1), int(x2), int(y2)),
            color=color,
            protect=protect,
            stroke_guard=stroke_guard,
            diag_band=diag_band,
            args=args,
        )
        if np.count_nonzero(wm_mask):
            total = cv2.bitwise_or(total, wm_mask)
            if preview is not None:
                preview[wm_mask > 0] = np.clip(color, 0, 255).astype(np.uint8)

    if bool(getattr(args, "_need_debug", False)):
        debug["colored_panel_wm_cleanup_mask"] = total
        if preview is not None:
            debug["colored_panel_wm_cleanup_preview"] = preview
    return out, debug


def preserve_color_panels_v69(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    protect: np.ndarray | None,
    diag_replace: np.ndarray | None,
    diag_band: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    V7.0 Speed Optimized Ordered Panel Edge Fix.

    Default behavior is conservative:
    1) colored panels are protected from broad diagonal corridor whitening;
    2) only the actual watermark candidate pixels inside the panel are recolored;
    3) any cyan/blue overflow that leaks outside the original panel geometry is
       trimmed back to white/paper inside the diagonal watermark corridor;
    4) residual pale cyan/gray anti-aliased edge remnants just outside the original panel are cleaned to white inside the diagonal corridor;
    5) full panel geometry rebuild remains disabled by default.
    """
    out = cleaned_bgr.copy()
    h, w = out.shape[:2]

    if not bool(getattr(args, "color_panel_preserve", True)):
        return out, {"color_panel_mask": np.zeros((h, w), dtype=np.uint8)}

    panel_mask, components, base_debug = detect_color_panel_geometry_v69(original_bgr, protect, args)
    if protect is None:
        protect, _ = build_content_protect_mask_v5(original_bgr, args)
    # Performance: when a caller already passes the page-level protect mask,
    # reuse it instead of recomputing HSV/Canny/morphology for the same page.

    gray_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    gray_c = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    spread_c = np.max(out, axis=2).astype(np.int16) - np.min(out, axis=2).astype(np.int16)

    stroke_guard = base_debug.get("color_panel_stroke_guard", protect)
    if diag_replace is None:
        diag_replace = np.zeros((h, w), dtype=np.uint8)
    if diag_band is None:
        diag_band = np.zeros((h, w), dtype=np.uint8)

    patch_mask_total = np.zeros((h, w), dtype=np.uint8)
    rebuild_mask_total = np.zeros((h, w), dtype=np.uint8)
    hole_mask_total = np.zeros((h, w), dtype=np.uint8)
    preview = out.copy()

    for comp in components:
        x1, y1, x2, y2 = comp["bbox"]  # type: ignore[index]
        color = np.asarray(comp["color"], dtype=np.float32)  # type: ignore[index]
        rect = np.zeros((h, w), dtype=bool)
        rect[int(y1):int(y2), int(x1):int(x2)] = True

        bg = rect & (stroke_guard == 0) & (gray_o >= int(args.color_panel_rebuild_gray_min))
        if not np.any(bg):
            continue

        # White holes: pixels that should belong to a colored panel but became nearly
        # neutral white after cleanup.  These indicate geometry/color loss.
        hole = (
            bg
            & (gray_c >= int(args.color_panel_hole_gray_min))
            & (sat_c <= int(args.color_panel_hole_sat_max))
            & (spread_c <= int(args.color_panel_hole_spread_max))
        )
        hole_mask_total[hole] = 255

        # Edge-zone hole ratio catches the visible jagged top/bottom panel edges.
        edge_band = np.zeros((h, w), dtype=bool)
        edge_px = max(2, int(args.color_panel_edge_check_px))
        edge_band[int(y1):min(h, int(y1) + edge_px), int(x1):int(x2)] = True
        edge_band[max(0, int(y2) - edge_px):int(y2), int(x1):int(x2)] = True
        edge_bg = bg & edge_band
        bg_count = max(1, int(np.count_nonzero(bg)))
        edge_bg_count = max(1, int(np.count_nonzero(edge_bg)))
        hole_ratio = float(np.count_nonzero(hole)) / float(bg_count)
        edge_hole_ratio = float(np.count_nonzero(hole & edge_band)) / float(edge_bg_count)

        should_rebuild = bool(getattr(args, "color_panel_rebuild_fallback", True)) and (
            hole_ratio >= float(args.color_panel_hole_ratio_trigger)
            or edge_hole_ratio >= float(args.color_panel_edge_hole_ratio_trigger)
        )

        if should_rebuild:
            # Fallback only: rebuild full background geometry, but keep strokes/borders.
            m = bg
            rebuild_mask_total[m] = 255
        else:
            # V6.9.2 Strict Local Color Panel Patch:
            # Never repaint a whole colored panel and never patch panels that are not
            # actually touched by the diagonal watermark cleanup.
            #
            # Only patch pixels that satisfy all of the following:
            #   1) inside panel background;
            #   2) inside the diagonal watermark band;
            #   3) actually selected by diag_replace (true watermark candidate) or a
            #      tiny white-hole artifact immediately adjacent to those pixels.
            strict_seed = bg & (diag_band > 0) & (diag_replace > 0)
            local_corridor = np.zeros_like(bg, dtype=bool)
            if bool(getattr(args, "color_panel_local_patch", True)) and np.any(strict_seed):
                lp = strict_seed.astype(np.uint8) * 255
                kx = max(1, int(getattr(args, "color_panel_local_patch_dilate_x", 9)))
                ky = max(1, int(getattr(args, "color_panel_local_patch_dilate_y", 3)))
                local_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
                local_corridor = cv2.dilate(lp, local_kernel, iterations=1) > 0
                # Keep the patch inside the colored panel background and strictly inside
                # the diagonal watermark corridor.
                local_corridor = local_corridor & bg & (diag_band > 0)

            # White-hole fallback is allowed only if it is inside the diagonal band and
            # spatially adjacent to the actual cleanup seed.  This prevents non-watermark
            # cyan panels elsewhere on the page from receiving extra color fill.
            local_hole = np.zeros_like(bg, dtype=bool)
            if np.any(hole) and np.any(strict_seed):
                near_seed = cv2.dilate(
                    strict_seed.astype(np.uint8) * 255,
                    cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
                    iterations=1,
                ) > 0
                local_hole = hole & near_seed & (diag_band > 0)

            m = strict_seed | local_corridor | local_hole
            patch_mask_total[m] = 255

        if np.any(m):
            alpha = float(args.color_panel_patch_alpha if not should_rebuild else args.color_panel_alpha)
            out[m] = np.clip(alpha * color + (1.0 - alpha) * out[m].astype(np.float32), 0, 255).astype(np.uint8)
            preview[m] = np.clip(color, 0, 255).astype(np.uint8)

        if bool(getattr(args, "colored_panel_watermark_cleanup", False)) and (not should_rebuild):
            out, wm_mask = _colored_panel_watermark_cleanup_component_v776(
                original_bgr=original_bgr,
                out=out,
                rect=(int(x1), int(y1), int(x2), int(y2)),
                color=color,
                protect=protect,
                stroke_guard=stroke_guard,
                diag_band=diag_band,
                args=args,
            )
            if np.count_nonzero(wm_mask):
                patch_mask_total = cv2.bitwise_or(patch_mask_total, wm_mask)
                preview[wm_mask > 0] = np.clip(color, 0, 255).astype(np.uint8)

    overflow_trim_mask = np.zeros((h, w), dtype=np.uint8)
    if bool(getattr(args, "color_panel_overflow_trim", True)) and np.any(panel_mask):
        # Detect current cyan-like fills after patching, then trim any colored overflow
        # that leaks outside the original panel geometry.  This is limited to the
        # diagonal watermark corridor so unrelated colored panels elsewhere stay intact.
        hsv_now = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
        hue_now = hsv_now[:, :, 0]
        sat_now = hsv_now[:, :, 1]
        val_now = hsv_now[:, :, 2]
        gray_now = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        spread_now = np.max(out, axis=2).astype(np.int16) - np.min(out, axis=2).astype(np.int16)

        current_cyan = (
            (hue_now >= int(args.color_panel_hue_min))
            & (hue_now <= int(args.color_panel_hue_max))
            & (sat_now >= int(args.color_panel_sat_min))
            & (sat_now <= int(args.color_panel_sat_max))
            & (val_now >= int(args.color_panel_value_min))
            & (gray_now >= int(args.color_panel_gray_min))
            & (spread_now >= int(args.color_panel_spread_min))
        ) | (
            (sat_now >= int(args.color_panel_extra_sat_min))
            & (sat_now <= int(args.color_panel_extra_sat_max))
            & (val_now >= int(args.color_panel_value_min))
            & (gray_now >= int(args.color_panel_gray_min))
            & (spread_now >= int(args.color_panel_spread_min))
        )
        current_u8 = (current_cyan.astype(np.uint8) * 255)
        current_u8 = cv2.morphologyEx(current_u8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (11, 5)), iterations=1)
        current_u8 = cv2.morphologyEx(current_u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)), iterations=1)

        orig_raw = base_debug.get("color_panel_raw", panel_mask)
        safe_kx = max(1, int(getattr(args, "color_panel_overflow_safe_dilate_x", 5)))
        safe_ky = max(1, int(getattr(args, "color_panel_overflow_safe_dilate_y", 3)))
        near_kx = max(safe_kx, int(getattr(args, "color_panel_overflow_near_dilate_x", 55)))
        near_ky = max(safe_ky, int(getattr(args, "color_panel_overflow_near_dilate_y", 13)))

        orig_safe = cv2.dilate(orig_raw, cv2.getStructuringElement(cv2.MORPH_RECT, (safe_kx, safe_ky)), iterations=1) > 0
        near_orig = cv2.dilate(orig_raw, cv2.getStructuringElement(cv2.MORPH_RECT, (near_kx, near_ky)), iterations=1) > 0

        overflow = (current_u8 > 0) & near_orig & (~orig_safe) & (diag_band > 0) & (stroke_guard == 0)

        # V6.9.4: also clean pale anti-aliased cyan/gray remnants that remain just
        # outside the true panel geometry.  These are often too faint to satisfy the
        # cyan detector but still visible as a thin stray line under/over the box.
        outside_extra = np.zeros((h, w), dtype=bool)
        if bool(getattr(args, "color_panel_outside_strip_clean", True)):
            strip_safe_x = max(1, int(getattr(args, "color_panel_outside_strip_safe_dilate_x", 3)))
            strip_safe_y = max(1, int(getattr(args, "color_panel_outside_strip_safe_dilate_y", 3)))
            strip_near_x = max(strip_safe_x, int(getattr(args, "color_panel_outside_strip_near_dilate_x", 41)))
            strip_near_y = max(strip_safe_y, int(getattr(args, "color_panel_outside_strip_near_dilate_y", 11)))
            strip_safe = cv2.dilate(orig_raw, cv2.getStructuringElement(cv2.MORPH_RECT, (strip_safe_x, strip_safe_y)), iterations=1) > 0
            strip_near = cv2.dilate(orig_raw, cv2.getStructuringElement(cv2.MORPH_RECT, (strip_near_x, strip_near_y)), iterations=1) > 0
            outside_strip = strip_near & (~strip_safe) & (diag_band > 0) & (stroke_guard == 0)
            pale_residual = (
                (gray_now >= int(getattr(args, "color_panel_outside_strip_gray_min", 208)))
                & (sat_now <= int(getattr(args, "color_panel_outside_strip_sat_max", 70)))
                & (spread_now <= int(getattr(args, "color_panel_outside_strip_spread_max", 42)))
            )
            outside_extra = outside_strip & (pale_residual | (current_u8 > 0))
            if np.any(outside_extra):
                close_x = max(1, int(getattr(args, "color_panel_outside_strip_close_x", 13)))
                close_y = max(1, int(getattr(args, "color_panel_outside_strip_close_y", 5)))
                outside_extra = cv2.morphologyEx((outside_extra.astype(np.uint8) * 255), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (close_x, close_y)), iterations=1) > 0
                outside_extra = outside_extra & outside_strip

        cleanup_mask = overflow | outside_extra
        cleanup_u8 = (cleanup_mask.astype(np.uint8) * 255)
        if np.any(cleanup_u8):
            white = np.array(getattr(args, "color_panel_overflow_white", [255,255,255]), dtype=np.uint8)
            out[cleanup_mask] = white
            preview[cleanup_mask] = white
            overflow_trim_mask[cleanup_mask] = 255

    debug = {
        **base_debug,
        "color_panel_patch_mask": patch_mask_total,
        "color_panel_rebuild_mask": rebuild_mask_total,
        "color_panel_hole_mask": hole_mask_total,
        "color_panel_overflow_trim_mask": overflow_trim_mask,
        "color_panel_preview": preview,
    }
    return out, debug



def page_has_color_panel_candidate_in_band(
    bgr: np.ndarray,
    band: np.ndarray | None,
    args: argparse.Namespace,
) -> bool:
    """V7 balanced-mode gate for expensive color-panel repair."""
    if band is None or not np.any(band):
        return False
    ys, xs = np.where(band > 0)
    if len(xs) == 0:
        return False
    pad = 12
    h, w = bgr.shape[:2]
    x1, x2 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad + 1)
    y1, y2 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad + 1)
    roi = bgr[y1:y2, x1:x2]
    band_roi = band[y1:y2, x1:x2] > 0
    if roi.size == 0 or np.count_nonzero(band_roi) < 50:
        return False

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hue = hsv[:, :, 0]
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    spread = np.max(roi, axis=2).astype(np.int16) - np.min(roi, axis=2).astype(np.int16)

    panel_like = (
        band_roi
        & (gray >= int(args.color_panel_gray_min))
        & (val >= int(args.color_panel_value_min))
        & (spread >= int(args.color_panel_spread_min))
        & (
            (
                (hue >= int(args.color_panel_hue_min))
                & (hue <= int(args.color_panel_hue_max))
                & (sat >= int(args.color_panel_sat_min))
                & (sat <= int(args.color_panel_sat_max))
            )
            | (
                (sat >= int(args.color_panel_extra_sat_min))
                & (sat <= int(args.color_panel_extra_sat_max))
            )
        )
    )
    return int(np.count_nonzero(panel_like)) >= int(getattr(args, "v7_panel_gate_min_pixels", 120))




def detect_table_colored_cells_v768(
    original_bgr: np.ndarray,
    band: np.ndarray | None,
    protect: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, list[dict[str, object]], dict[str, np.ndarray]]:
    """
    V7.6.8: detect colored answer/table cells (cyan/pink Đ-S columns) as cells,
    not as long horizontal panels.  The older color_panel detector was tuned for
    wide highlighted strips and misses narrow vertical answer columns.  This
    detector is deliberately local: components are used only where they intersect
    the diagonal watermark band.
    """
    h, w = original_bgr.shape[:2]
    need_debug = bool(getattr(args, "_need_debug", False))
    debug: dict[str, np.ndarray] = {}
    if band is None:
        band = np.zeros((h, w), dtype=np.uint8)
    if protect is None:
        protect = np.zeros((h, w), dtype=np.uint8)

    # V7.7.4 performance: table-cell detection is needed by both the
    # table-aware cleanup pass and the graph table-protection pass.  For a
    # single page these calls use the same original image, band and protect
    # masks, so cache the exact result on the per-page args object.
    cache_key = (id(original_bgr), id(band), id(protect), h, w)
    if bool(getattr(args, "v774_cache_table_cells", True)):
        cached = getattr(args, "_v774_table_cell_cache", None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == cache_key:
            return cached[1]

    gray = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    spread = np.max(original_bgr, axis=2).astype(np.int16) - np.min(original_bgr, axis=2).astype(np.int16)

    # Pale table backgrounds are light, moderately saturated, and have a small but
    # stable channel spread.  Exclude strong strokes using the existing protect mask.
    raw = (
        (protect == 0)
        & (gray >= int(getattr(args, "table_cell_gray_min", 165)))
        & (val >= int(getattr(args, "table_cell_value_min", 168)))
        & (sat >= int(getattr(args, "table_cell_sat_min", 8)))
        & (sat <= int(getattr(args, "table_cell_sat_max", 135)))
        & (spread >= int(getattr(args, "table_cell_spread_min", 5)))
    )
    raw_u8 = raw.astype(np.uint8) * 255
    raw_u8 = cv2.morphologyEx(raw_u8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
    raw_u8 = cv2.morphologyEx(raw_u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(raw_u8, connectivity=8)
    cell_mask = np.zeros((h, w), dtype=np.uint8)
    components: list[dict[str, object]] = []

    # Make the band slightly wider for intersection testing only; the final patch
    # still clips to the original diagonal band.
    band_near = cv2.dilate(
        (band > 0).astype(np.uint8) * 255,
        cv2.getStructuringElement(cv2.MORPH_RECT, (
            max(3, int(getattr(args, "table_band_intersect_dilate_x", 13))),
            max(3, int(getattr(args, "table_band_intersect_dilate_y", 7))),
        )),
        iterations=1,
    ) > 0

    min_area = int(getattr(args, "table_cell_min_area", 250))
    min_w = int(getattr(args, "table_cell_min_width_px", 12))
    min_h = int(getattr(args, "table_cell_min_height_px", 12))
    max_w_ratio = float(getattr(args, "table_cell_max_width_ratio", 0.24))
    max_h_ratio = float(getattr(args, "table_cell_max_height_ratio", 0.40))
    min_cov = float(getattr(args, "table_cell_min_coverage", 0.22))
    min_sample = int(getattr(args, "table_cell_min_sample_pixels", 40))

    for lab in range(1, n):
        x, y, ww, hh, area = [int(v) for v in stats[lab]]
        if area < min_area or ww < min_w or hh < min_h:
            continue
        if ww > max(1, int(max_w_ratio * w)) or hh > max(1, int(max_h_ratio * h)):
            # Prevent a broad illustration/background from being treated as a cell.
            continue
        if (area / max(1, ww * hh)) < min_cov:
            continue
        comp = labels == lab
        if np.count_nonzero(comp & band_near) < int(getattr(args, "table_cell_min_band_pixels", 6)):
            continue

        sample = comp & (protect == 0) & (~(band_near))
        if np.count_nonzero(sample) < min_sample:
            sample = comp & (protect == 0)
        if np.count_nonzero(sample) < max(8, min_sample // 3):
            continue
        color = np.median(original_bgr[sample], axis=0).astype(np.float32)
        if float(np.max(color) - np.min(color)) < float(getattr(args, "table_cell_min_color_spread", 5.0)):
            continue

        cell_mask[comp] = 255
        components.append({"bbox": (x, y, x + ww, y + hh), "mask_label": lab, "color": color})

    if need_debug:
        debug["table_cell_raw"] = raw_u8
        debug["table_cell_mask"] = cell_mask

    result = (cell_mask, components, debug)
    if bool(getattr(args, "v774_cache_table_cells", True)):
        setattr(args, "_v774_table_cell_cache", (cache_key, result))
    return result


def restore_table_borders_and_circles_v768(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    table_near: np.ndarray,
    band: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Restore table/circle strokes only inside the diagonal watermark/table intersection.
    We do not copy whole original regions, only long table borders and compact circle/text
    strokes, so watermark ghosts from the original are not reintroduced broadly.
    """
    h, w = cleaned_bgr.shape[:2]
    gray_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    hsv_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
    sat_o = hsv_o[:, :, 1]

    stroke_like = (
        (gray_o <= int(getattr(args, "table_stroke_gray_max", 185)))
        | ((sat_o >= int(getattr(args, "table_stroke_sat_min", 30))) & (gray_o <= int(getattr(args, "table_stroke_color_gray_max", 240))))
    )
    stroke_u8 = stroke_like.astype(np.uint8) * 255

    # Long horizontal/vertical borders.
    h_len = max(15, int(getattr(args, "table_border_min_len_x", 28)))
    v_len = max(15, int(getattr(args, "table_border_min_len_y", 28)))
    hline = cv2.morphologyEx(stroke_u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1)), iterations=1)
    vline = cv2.morphologyEx(stroke_u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len)), iterations=1)
    border = cv2.bitwise_or(hline, vline) > 0

    # Compact strokes include answer circles and Đ/S labels; filter CCs so random
    # watermark fragments are rejected.
    try:
        edges = cv2.Canny(gray_o, int(getattr(args, "table_circle_canny_low", 35)), int(getattr(args, "table_circle_canny_high", 120))) > 0
    except Exception:
        edges = np.zeros((h, w), dtype=bool)
    compact_seed = (edges | stroke_like) & (table_near > 0)
    compact_u8 = compact_seed.astype(np.uint8) * 255
    compact_u8 = cv2.morphologyEx(compact_u8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)), iterations=1)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(compact_u8, 8)
    compact = np.zeros((h, w), dtype=bool)
    min_area = int(getattr(args, "table_compact_stroke_min_area", 8))
    max_area = int(getattr(args, "table_compact_stroke_max_area", 2200))
    max_w = int(getattr(args, "table_compact_stroke_max_w", 95))
    max_h = int(getattr(args, "table_compact_stroke_max_h", 95))
    for i in range(1, n):
        x, y, ww, hh, area = [int(v) for v in stats[i]]
        if area < min_area or area > max_area or ww > max_w or hh > max_h:
            continue
        # Answer circles are roughly square; labels Đ/S are compact but not huge.
        if ww < 2 or hh < 2:
            continue
        compact |= labels == i

    restore = (border | compact) & (table_near > 0) & (band > 0)
    if bool(getattr(args, "table_restore_stroke_dilate", True)):
        restore = cv2.dilate(
            restore.astype(np.uint8) * 255,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1)),
            iterations=1,
        ) > 0
        restore &= (table_near > 0) & (band > 0) & stroke_like

    out = cleaned_bgr.copy()
    out[restore] = original_bgr[restore]
    return out, restore.astype(np.uint8) * 255


def table_aware_watermark_cleanup_v768(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    protect: np.ndarray | None,
    diag_replace: np.ndarray | None,
    band: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    V7.6.8: table-aware cleanup for colored Đ/S answer tables.

    The v767 ROI background cleaner works well on white ruled paper, but colored
    answer cells need a different model: per-cell background color, table border
    restoration, and answer-circle stroke restoration.  This pass is clipped to
    watermark ∩ table only, so it does not repaint the whole table.
    """
    out = cleaned_bgr.copy()
    h, w = out.shape[:2]
    need_debug = bool(getattr(args, "_need_debug", False))
    debug: dict[str, np.ndarray] = {}
    if not bool(getattr(args, "table_aware_cleanup", True)):
        return out, debug
    if band is None or np.count_nonzero(band) == 0:
        return out, debug
    if protect is None:
        protect = np.zeros((h, w), dtype=np.uint8)

    cell_mask, components, cell_debug = detect_table_colored_cells_v768(original_bgr, band, protect, args)
    if need_debug:
        debug.update(cell_debug)
    if not components or np.count_nonzero(cell_mask) == 0:
        return out, debug

    # Table-near mask: expand colored cells enough to include adjacent borders and
    # answer circles, but final patches are still clipped to the diagonal band.
    near_x = max(1, int(getattr(args, "table_near_dilate_x", 18)))
    near_y = max(1, int(getattr(args, "table_near_dilate_y", 10)))
    table_near = cv2.dilate(cell_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (near_x, near_y)), iterations=1)

    band_bool = band > 0
    gray_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    hsv_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
    sat_o = hsv_o[:, :, 1]
    gray_c = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    hsv_c = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    sat_c = hsv_c[:, :, 1]
    spread_c = np.max(out, axis=2).astype(np.int16) - np.min(out, axis=2).astype(np.int16)

    stroke_like_o = (
        (gray_o <= int(getattr(args, "table_stroke_gray_max", 185)))
        | ((sat_o >= int(getattr(args, "table_stroke_sat_min", 30))) & (gray_o <= int(getattr(args, "table_stroke_color_gray_max", 240))))
    )
    stroke_like_c = (
        (gray_c <= int(getattr(args, "table_stroke_gray_max", 185)))
        | ((sat_c >= int(getattr(args, "table_stroke_sat_min", 30))) & (gray_c <= int(getattr(args, "table_stroke_color_gray_max", 240))))
    )
    # The generic protect mask often marks pale colored cell backgrounds as
    # "colored ink".  For table background repair this would block the whole
    # cell, so subtract the detected colored-cell fill and keep only true strokes.
    protect_strokes = (protect > 0) & ~(cell_mask > 0)
    stroke_guard = cv2.dilate((stroke_like_o | stroke_like_c | protect_strokes).astype(np.uint8) * 255,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1) > 0

    bg_patch_total = np.zeros((h, w), dtype=bool)
    labels_mask = np.zeros((h, w), dtype=np.int32)
    # Re-label the cell mask so each colored cell/column region receives its own median.
    n, labels, stats, _ = cv2.connectedComponentsWithStats((cell_mask > 0).astype(np.uint8), 8)
    for lab in range(1, n):
        comp = labels == lab
        if np.count_nonzero(comp & band_bool) < int(getattr(args, "table_cell_min_band_pixels", 6)):
            continue
        sample = comp & (~band_bool) & (~stroke_guard)
        if np.count_nonzero(sample) < int(getattr(args, "table_cell_min_sample_pixels", 40)):
            sample = comp & (~stroke_guard)
        if np.count_nonzero(sample) < 8:
            continue
        color = np.median(original_bgr[sample], axis=0).astype(np.float32)

        # Patch only colored-cell background inside the diagonal band.  Require the
        # cleaned pixel to look like damaged/ghosted background or belong to a known
        # changed area; this avoids recoloring clean strokes.
        damaged_like = (
            (gray_c >= int(getattr(args, "table_bg_patch_gray_min", 155)))
            & (sat_c <= int(getattr(args, "table_bg_patch_sat_max", 180)))
            & (spread_c <= int(getattr(args, "table_bg_patch_spread_max", 120)))
        )
        if diag_replace is not None and np.count_nonzero(diag_replace) > 0:
            seed = cv2.dilate((diag_replace > 0).astype(np.uint8) * 255,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (
                                  max(3, int(getattr(args, "table_bg_patch_diag_dilate_x", 13))),
                                  max(3, int(getattr(args, "table_bg_patch_diag_dilate_y", 5))),
                              )), iterations=1) > 0
            damaged_like = damaged_like | seed
        m = comp & band_bool & damaged_like & (~stroke_guard)
        if np.any(m):
            alpha = float(getattr(args, "table_bg_patch_alpha", 1.0))
            alpha = max(0.0, min(1.0, alpha))
            out[m] = np.clip(alpha * color + (1.0 - alpha) * out[m].astype(np.float32), 0, 255).astype(np.uint8)
            bg_patch_total |= m
            labels_mask[m] = lab

    # Restore table borders, D/S labels and answer circles after background repair.
    out, stroke_restore = restore_table_borders_and_circles_v768(original_bgr, out, table_near, band, args)

    if need_debug:
        debug["table_aware_bg_patch_mask"] = bg_patch_total.astype(np.uint8) * 255
        debug["table_aware_stroke_restore_mask"] = stroke_restore
        debug["table_aware_near_mask"] = table_near
    return out, debug

def page_has_ruled_lines_fast(bgr: np.ndarray, args: argparse.Namespace) -> bool:
    """V7 balanced-mode fast periodic-row test before full line restoration."""
    h, w = bgr.shape[:2]
    y1 = max(0, int(float(args.line_detect_y_min) * h))
    y2 = min(h, int(float(args.line_detect_y_max) * h))
    x1 = max(0, int(float(args.line_detect_x_min) * w))
    x2 = min(w, int(float(args.line_detect_x_max) * w))
    if y2 - y1 < 120 or x2 - x1 < 200:
        return False
    gray = cv2.cvtColor(bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    if gray.shape[1] > 700:
        gray = cv2.resize(gray, (700, gray.shape[0]), interpolation=cv2.INTER_AREA)
    gray[gray < int(args.line_detect_dark_exclude_gray)] = 255
    row_mean = gray.mean(axis=1).astype(np.float32)
    if len(row_mean) < 50:
        return False
    baseline = median_filter1d_v6(row_mean, 31)
    score = np.clip(baseline - row_mean, 0, None)
    positive = score > max(1.5, float(np.percentile(score, 85)))
    if np.count_nonzero(positive) < int(getattr(args, "v7_line_gate_min_rows", 18)):
        return False
    rows = np.where(positive)[0]
    return bool(len(rows) and (rows[-1] - rows[0]) >= int(0.35 * (y2 - y1)))


def apply_v7_mode_presets(args: argparse.Namespace) -> argparse.Namespace:
    """
    quality  = run all safeguard modules.
    balanced = ROI-first plus conditional color-panel and line modules.
    """
    mode = str(getattr(args, "mode", "balanced")).lower()
    if mode not in {"quality", "balanced"}:
        mode = "balanced"
    args.mode = mode

    if mode == "quality":
        args.v7_conditional_color_panel = False
        args.v7_conditional_line_restore = False
        args.line_restore_damaged_only = True
        # Quality mode favors content safety over aggressive removal.
        args.safe_text_aa_enable = True
        args.safe_text_aa_gray_max = max(int(getattr(args, "safe_text_aa_gray_max", 145)), 150)
        args.safe_text_aa_min_contrast = min(int(getattr(args, "safe_text_aa_min_contrast", 42)), 38)
        args.safe_protect_dilate = max(int(getattr(args, "safe_protect_dilate", 1)), 1)
        if getattr(args, "jpeg_quality", 95) < 95:
            args.jpeg_quality = 95
    else:
        args.v7_conditional_color_panel = True
        args.v7_conditional_line_restore = True
        args.line_restore_damaged_only = True
        if getattr(args, "jpeg_quality", 95) > 92:
            args.jpeg_quality = 92
    return args



# -----------------------------------------------------------------------------
# V7.6.9 graph-stroke restore: safe, ROI-only, table-protected.
# -----------------------------------------------------------------------------
def build_table_protection_mask_v769(
    original_bgr: np.ndarray,
    band: np.ndarray | None,
    protect: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Hard-exclude colored answer tables and nearby table strokes from graph restore.

    The graph module must not touch Đ/S answer cells, table borders, or answer
    circles.  We reuse the V768 colored-cell detector, then add long table
    border strokes around those cells.
    """
    h, w = original_bgr.shape[:2]
    if band is None:
        band = np.zeros((h, w), dtype=np.uint8)
    if protect is None:
        protect = np.zeros((h, w), dtype=np.uint8)

    cell_mask, _components, cell_debug = detect_table_colored_cells_v768(original_bgr, band, protect, args)
    near_x = max(3, int(getattr(args, "graph_table_exclude_dilate_x", 24)))
    near_y = max(3, int(getattr(args, "graph_table_exclude_dilate_y", 16)))
    table_near = cv2.dilate(
        cell_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (near_x, near_y)),
        iterations=1,
    ) if np.count_nonzero(cell_mask) else np.zeros((h, w), dtype=np.uint8)

    # Strengthen table hard mask with long horizontal/vertical strokes close to cells.
    if np.count_nonzero(table_near):
        gray = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        stroke_like = ((gray <= int(getattr(args, "table_stroke_gray_max", 185))) |
                       ((sat >= int(getattr(args, "table_stroke_sat_min", 70))) &
                        (gray <= int(getattr(args, "table_stroke_color_gray_max", 225)))))
        stroke_u8 = (stroke_like.astype(np.uint8) * 255)
        h_len = max(21, int(getattr(args, "graph_table_border_min_len_x", 34)))
        v_len = max(21, int(getattr(args, "graph_table_border_min_len_y", 34)))
        hline = cv2.morphologyEx(stroke_u8, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1)), iterations=1)
        vline = cv2.morphologyEx(stroke_u8, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len)), iterations=1)
        table_strokes = cv2.bitwise_or(hline, vline)
        table_strokes = cv2.bitwise_and(table_strokes, cv2.dilate(table_near, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)), iterations=1))
        table_near = cv2.bitwise_or(table_near, cv2.dilate(table_strokes, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=1))

    if bool(getattr(args, "_need_debug", False)):
        return table_near, {
            "graph_table_protection_mask": table_near,
            "graph_table_cell_mask": cell_debug.get("table_cell_mask", cell_mask) if isinstance(cell_debug, dict) else cell_mask,
        }
    return table_near, {}


def _component_keep_long(mask_u8: np.ndarray, min_area: int, min_span: int) -> np.ndarray:
    """Keep only components that are plausibly graph strokes/curves, not small text specks."""
    if np.count_nonzero(mask_u8) == 0:
        return np.zeros_like(mask_u8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    keep = np.zeros(n, dtype=bool)
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        ww = int(stats[i, cv2.CC_STAT_WIDTH])
        hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if area >= min_area and max(ww, hh) >= min_span:
            keep[i] = True
    return (keep[labels].astype(np.uint8) * 255)


def restore_graph_strokes_inside_watermark_roi_v769(
    original_bgr: np.ndarray,
    cleaned_bgr: np.ndarray,
    protect: np.ndarray | None,
    diag_replace: np.ndarray | None,
    band: np.ndarray | None,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Safe graph-specific stroke restore, only inside watermark ROI.

    This module is deliberately conservative:
    - it never runs outside the diagonal/watermark ROI;
    - it hard-excludes colored answer-table regions;
    - it restores only stroke pixels that are visible in the original but missing
      or substantially weakened in the cleaned image;
    - it does not infer text labels or rewrite formulas.
    """
    out = cleaned_bgr.copy()
    h, w = out.shape[:2]
    need_debug = bool(getattr(args, "_need_debug", False))
    debug: dict[str, np.ndarray] = {}
    if not bool(getattr(args, "graph_restore_enabled", True)):
        return out, debug
    if protect is None:
        protect = np.zeros((h, w), dtype=np.uint8)
    if diag_replace is None:
        diag_replace = np.zeros((h, w), dtype=np.uint8)
    if band is None:
        band = np.zeros((h, w), dtype=np.uint8)

    # Strict ROI: only the watermark/diagonal affected area, optionally dilated a little.
    roi = ((diag_replace > 0) | (band > 0)).astype(np.uint8) * 255
    if np.count_nonzero(roi) == 0:
        return out, debug
    dx = max(0, int(getattr(args, "graph_roi_dilate_x", 6)))
    dy = max(0, int(getattr(args, "graph_roi_dilate_y", 3)))
    if dx or dy:
        roi = cv2.dilate(roi, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * dx + 1, 2 * dy + 1)), iterations=1)

    table_mask, table_debug = build_table_protection_mask_v769(original_bgr, band, protect, args)
    if need_debug:
        debug.update(table_debug)
    roi_bool = (roi > 0) & (table_mask == 0)
    if need_debug:
        debug["graph_restore_roi"] = (roi_bool.astype(np.uint8) * 255)
    if np.count_nonzero(roi_bool) < int(getattr(args, "graph_restore_min_roi_pixels", 40)):
        return out, debug

    gray_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
    gray_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2GRAY)
    hsv_o = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2HSV)
    hsv_c = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2HSV)
    sat_o = hsv_o[:, :, 1]
    sat_c = hsv_c[:, :, 1]
    hue_o = hsv_o[:, :, 0]
    hue_c = hsv_c[:, :, 0]

    # 1) Axes / straight / dashed dark graph strokes.
    black_gray_max = int(getattr(args, "graph_black_gray_max", 120))
    black_aa_gray_max = int(getattr(args, "graph_black_aa_gray_max", 170))
    # Dark seed, with edges to catch anti-aliased dashed strokes without grabbing pale paper lines.
    try:
        edges_o = cv2.Canny(gray_o, int(getattr(args, "graph_edge_canny_low", 24)), int(getattr(args, "graph_edge_canny_high", 105))) > 0
    except Exception:
        edges_o = np.zeros((h, w), dtype=bool)
    dark_seed = ((gray_o <= black_gray_max) | ((gray_o <= black_aa_gray_max) & edges_o)).astype(np.uint8) * 255

    # Extract long/dashed graph strokes while avoiding ordinary glyph blobs.
    line_len = max(15, int(getattr(args, "graph_line_min_len", 28)))
    dash_close = max(3, int(getattr(args, "graph_dashed_close_px", 17)))
    dark_closed_h = cv2.morphologyEx(dark_seed, cv2.MORPH_CLOSE,
                                     cv2.getStructuringElement(cv2.MORPH_RECT, (dash_close, 1)), iterations=1)
    dark_closed_v = cv2.morphologyEx(dark_seed, cv2.MORPH_CLOSE,
                                     cv2.getStructuringElement(cv2.MORPH_RECT, (1, dash_close)), iterations=1)
    hline = cv2.morphologyEx(dark_closed_h, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (line_len, 1)), iterations=1)
    vline = cv2.morphologyEx(dark_closed_v, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (1, line_len)), iterations=1)
    black_lines = cv2.bitwise_or(hline, vline)
    black_lines = cv2.bitwise_and(black_lines, dark_seed)
    black_lines = cv2.dilate(black_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)

    # Only restore where cleaned image no longer contains a comparable dark stroke.
    cleaned_dark = ((gray_c <= black_aa_gray_max) | (((gray_c <= 195) & (cv2.Canny(gray_c, 24, 105) > 0)))).astype(np.uint8) * 255
    cleaned_dark = cv2.dilate(cleaned_dark, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
    # Treat strokes as missing not only when absent, but also when the cleaner
    # weakened them noticeably inside the watermark ROI.
    weaken_black = (gray_c.astype(np.int16) - gray_o.astype(np.int16)) >= int(getattr(args, "graph_black_weaken_diff", 10))
    missing_black = (black_lines > 0) & roi_bool & ((cleaned_dark == 0) | weaken_black)

    # 2) Blue graph curves (sin/cos/tan/cot curves), but not blue D/S table labels due hard table mask.
    hl = int(getattr(args, "graph_blue_hue_min", 88))
    hh = int(getattr(args, "graph_blue_hue_max", 142))
    blue_o = ((hue_o >= hl) & (hue_o <= hh) &
              (sat_o >= int(getattr(args, "graph_blue_sat_min", 35))) &
              (gray_o <= int(getattr(args, "graph_blue_gray_max", 245))))
    # Extra BGR dominance guard for saturated blue printed curves.
    b = original_bgr[:, :, 0].astype(np.int16)
    g = original_bgr[:, :, 1].astype(np.int16)
    r = original_bgr[:, :, 2].astype(np.int16)
    blue_o |= ((b >= r + int(getattr(args, "graph_blue_b_minus_r_min", 20))) &
               (b >= g + int(getattr(args, "graph_blue_b_minus_g_min", 5))) &
               (sat_o >= int(getattr(args, "graph_blue_sat_min_alt", 25))) &
               (gray_o <= int(getattr(args, "graph_blue_gray_max", 245))))
    blue_u8 = (blue_o.astype(np.uint8) * 255)
    blue_u8 = _component_keep_long(
        blue_u8,
        min_area=int(getattr(args, "graph_blue_min_component_area", 8)),
        min_span=int(getattr(args, "graph_blue_min_component_span", 9)),
    )
    blue_u8 = cv2.dilate(blue_u8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)

    blue_c = ((hue_c >= hl) & (hue_c <= hh) &
              (sat_c >= int(getattr(args, "graph_blue_sat_min_clean", 25))) &
              (gray_c <= int(getattr(args, "graph_blue_gray_max", 245)))).astype(np.uint8) * 255
    blue_c = cv2.dilate(blue_c, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)
    blue_color_delta = np.max(np.abs(cleaned_bgr.astype(np.int16) - original_bgr.astype(np.int16)), axis=2) >= int(getattr(args, "graph_blue_weaken_diff", 10))
    blue_sat_loss = (sat_o.astype(np.int16) - sat_c.astype(np.int16)) >= int(getattr(args, "graph_blue_sat_loss", 12))
    missing_blue = (blue_u8 > 0) & roi_bool & ((blue_c == 0) | blue_color_delta | blue_sat_loss)

    # 3) Markers/dots: small blue/black dots, only if graph area already has lines/curves nearby.
    marker_mask = np.zeros((h, w), dtype=bool)
    if bool(getattr(args, "graph_restore_markers", True)):
        graph_near = cv2.dilate(cv2.bitwise_or(black_lines, blue_u8), cv2.getStructuringElement(cv2.MORPH_RECT, (31, 31)), iterations=1) > 0
        # compact dark/blue components inside ROI, near graph strokes.
        compact_seed = ((gray_o <= int(getattr(args, "graph_marker_gray_max", 130))) | (blue_u8 > 0)).astype(np.uint8) * 255
        n, labels, stats, _ = cv2.connectedComponentsWithStats(compact_seed, connectivity=8)
        keep = np.zeros(n, dtype=bool)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA]); ww = int(stats[i, cv2.CC_STAT_WIDTH]); hh2 = int(stats[i, cv2.CC_STAT_HEIGHT])
            if int(getattr(args, "graph_marker_min_area", 3)) <= area <= int(getattr(args, "graph_marker_max_area", 180)) and ww <= 22 and hh2 <= 22:
                keep[i] = True
        markers = keep[labels]
        cleaned_marker_present = cv2.dilate((((gray_c <= 170) | (blue_c > 0)).astype(np.uint8) * 255), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1) > 0
        marker_mask = markers & roi_bool & graph_near & (~cleaned_marker_present)

    # Protect ordinary formula/text labels: do not expand the mask into them.  A very small dilation
    # prevents restored graph pixels from touching glyph anti-aliases.
    text_guard = (protect > 0)
    # Keep graph strokes that are clearly black/blue even if protect includes them; guard only high-contrast blobs
    # outside our extracted line/curve candidates.
    candidates = missing_black | missing_blue | marker_mask
    min_pixels = int(getattr(args, "graph_restore_min_pixels", 4))
    if need_debug:
        debug["graph_restore_black_strokes"] = (missing_black.astype(np.uint8) * 255)
        debug["graph_restore_blue_curves"] = (missing_blue.astype(np.uint8) * 255)
        debug["graph_restore_markers"] = (marker_mask.astype(np.uint8) * 255)

    if np.count_nonzero(candidates) < min_pixels:
        return out, debug

    # Confidence: enough extracted graph stroke evidence in the ROI, no overlap with tables.
    stroke_evidence = ((black_lines > 0) | (blue_u8 > 0)) & roi_bool
    evidence_pixels = int(np.count_nonzero(stroke_evidence))
    restore_pixels = int(np.count_nonzero(candidates))
    if evidence_pixels < int(getattr(args, "graph_restore_min_evidence_pixels", 12)):
        return out, debug
    confidence = min(1.0, restore_pixels / max(float(evidence_pixels), 1.0) * 3.0)
    # If the fraction is tiny, still allow if there are high confidence stroke masks (this is a small gap).
    if confidence < float(getattr(args, "graph_restore_min_confidence", 0.05)):
        return out, debug
    if need_debug:
        debug["graph_restore_confidence_mask"] = (stroke_evidence.astype(np.uint8) * 255)

    # One-pixel restoration is often too sparse after PNG rendering; expand only within original stroke mask.
    restore = candidates.astype(np.uint8) * 255
    if bool(getattr(args, "graph_restore_dilate", True)):
        restore = cv2.dilate(restore, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
        # After dilation, constrain back to original graph stroke neighborhoods and ROI/table guards.
        graph_neighborhood = cv2.dilate(cv2.bitwise_or(black_lines, blue_u8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1) > 0
        restore = ((restore > 0) & graph_neighborhood & roi_bool).astype(np.uint8) * 255

    # Hard guarantee: no graph module modifications inside detected answer tables or outside ROI.
    restore = (restore > 0) & roi_bool & (table_mask == 0)
    if need_debug:
        debug["graph_restore_mask"] = (restore.astype(np.uint8) * 255)
    if np.count_nonzero(restore) == 0:
        return out, debug

    # Copy only true graph-stroke pixels from the original. This is safer than vector-guessing and it
    # preserves exact antialiasing/color where the original graph stroke remains visible under watermark.
    out[restore] = original_bgr[restore]
    return out, debug

def clean_page_bgr(bgr: np.ndarray, args: argparse.Namespace) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    # V7.7.4: clear per-page caches before any processing.  This avoids stale
    # cache reuse if a legacy CLI path reuses the same args object across pages.
    if hasattr(args, "_v774_table_cell_cache"):
        try:
            delattr(args, "_v774_table_cell_cache")
        except Exception:
            setattr(args, "_v774_table_cell_cache", None)

    # Keep the page after header/footer cleanup as the line-restoration reference.
    # Header/footer whitening should not affect the ruled-paper grid, but removing it
    # first avoids treating top/bottom watermark strokes as content.
    cleaned, hf_mask = guided_header_footer_cleanup(bgr, args)
    line_reference = cleaned.copy()
    cleaned, diag_debug = smart_diagonal_cleanup_v5(cleaned, args)
    if not bool(getattr(args, "_need_debug", False)):
        # Keep only masks required by downstream steps; release heavy diagnostics early.
        diag_debug = {k: v for k, v in diag_debug.items() if k in {"diag_replace", "safe_protect", "diag_band"}}

    # V7.6.5 ordering:
    # Run panel/ghost cleanup first, then apply ruled-line reconstruction as the true
    # final pass. v763 restored the lines before the panel pass, so the last cleanup
    # could whiten synthetic line segments again and leave dashed gaps.
    panel_debug: dict[str, np.ndarray] = {}
    run_panel = bool(getattr(args, "color_panel_preserve", True))
    if run_panel and bool(getattr(args, "v7_conditional_color_panel", False)):
        run_panel = bool(getattr(args, "_v7_last_color_panel_needed", False))
    if run_panel:
        cleaned, panel_debug = preserve_color_panels_v69(
            line_reference,
            cleaned,
            diag_debug.get("safe_protect"),
            diag_debug.get("diag_replace"),
            diag_debug.get("diag_band"),
            args,
        )
        if not bool(getattr(args, "_need_debug", False)):
            panel_debug = {}

    # V7.6.7: clean the actual diagonal watermark ROI background before the
    # final ruled-line pass. This makes line restoration a replace-clean operation
    # inside the watermark, not an added line layer over dirty/ghosted paper.
    roi_bg_debug: dict[str, np.ndarray] = {}
    if bool(getattr(args, "watermark_roi_background_reconstruct", True)):
        cleaned, roi_bg_debug = reconstruct_watermark_roi_background_v767(
            cleaned,
            line_reference,
            diag_debug.get("diag_replace"),
            diag_debug.get("safe_protect"),
            diag_debug.get("diag_band"),
            args,
        )
        if not bool(getattr(args, "_need_debug", False)):
            roi_bg_debug = {}

    # V7.6.8: table-aware pass.  Colored Đ/S answer cells need per-cell
    # background restoration and table/circle stroke restoration; do this after
    # generic ROI cleanup so the table pass wins inside watermark ∩ table.
    table_debug: dict[str, np.ndarray] = {}
    if bool(getattr(args, "table_aware_cleanup", True)):
        cleaned, table_debug = table_aware_watermark_cleanup_v768(
            line_reference,
            cleaned,
            diag_debug.get("safe_protect"),
            diag_debug.get("diag_replace"),
            diag_debug.get("diag_band"),
            args,
        )
        if not bool(getattr(args, "_need_debug", False)):
            table_debug = {}

    # V7.6.9: graph-aware stroke restore. This pass is isolated from Đ/S
    # answer tables and only modifies watermark ROI pixels; it restores graph
    # axes/dashed strokes/blue curves that the watermark cleaner weakened.
    graph_debug: dict[str, np.ndarray] = {}
    if bool(getattr(args, "graph_restore_enabled", True)):
        cleaned, graph_debug = restore_graph_strokes_inside_watermark_roi_v769(
            line_reference,
            cleaned,
            diag_debug.get("safe_protect"),
            diag_debug.get("diag_replace"),
            diag_debug.get("diag_band"),
            args,
        )
        if not bool(getattr(args, "_need_debug", False)):
            graph_debug = {}

    # V7.7.6: graph restore can re-copy faint original watermark strokes on flat
    # cyan panels because it sees them as weakened strokes.  Run one final
    # flat-panel-only cleanup after graph restore, before ruled-line reconstruction.
    panel_wm_debug: dict[str, np.ndarray] = {}
    if bool(getattr(args, "colored_panel_watermark_cleanup", False)):
        cleaned, panel_wm_debug = colored_panel_watermark_cleanup_v776(
            line_reference,
            cleaned,
            diag_debug.get("safe_protect"),
            diag_debug.get("diag_band"),
            args,
        )
        if not bool(getattr(args, "_need_debug", False)):
            panel_wm_debug = {}

    line_debug: dict[str, np.ndarray] = {}
    run_line_restore = bool(args.line_restore)
    if run_line_restore and bool(getattr(args, "v7_conditional_line_restore", False)):
        run_line_restore = page_has_ruled_lines_fast(line_reference, args)
    if run_line_restore:
        cleaned, line_debug = restore_ruled_lines_v6(
            cleaned,
            line_reference,
            diag_debug.get("diag_replace"),
            diag_debug.get("safe_protect"),
            diag_debug.get("diag_band"),
            args,
        )
        if not bool(getattr(args, "_need_debug", False)):
            line_debug = {}

    if bool(getattr(args, "_need_debug", False)):
        debug = {"header_footer_mask": hf_mask, **diag_debug, **panel_debug, **roi_bg_debug, **table_debug, **graph_debug, **panel_wm_debug, **line_debug}
    else:
        debug = {}
    return cleaned, debug


def encode_image_for_pdf(bgr: np.ndarray, image_format: str, jpeg_quality: int) -> bytes:
    fmt = image_format.lower().strip(".")
    if fmt in {"jpg", "jpeg"}:
        ok, encoded = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    elif fmt == "png":
        ok, encoded = cv2.imencode(".png", bgr, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
    else:
        raise ValueError("image_format must be jpg or png")
    if not ok:
        raise RuntimeError("Cannot encode cleaned page image")
    return encoded.tobytes()



def process_single_page_to_jpg(input_pdf: Path, page_index: int, output_jpg: Path, args: argparse.Namespace) -> tuple[float, float]:
    """Worker-safe single page renderer/cleaner. page_index is zero-based."""
    setattr(args, "_need_debug", False)
    input_pdf = input_pdf.expanduser().resolve()
    output_jpg = output_jpg.expanduser().resolve()
    output_jpg.parent.mkdir(parents=True, exist_ok=True)
    src = fitz.open(str(input_pdf))
    try:
        page = src.load_page(int(page_index))
        width = float(page.rect.width)
        height = float(page.rect.height)
        bgr = render_page_to_bgr(page, int(args.dpi))
    finally:
        src.close()
    cleaned, _ = clean_page_bgr(bgr, args)
    ok, encoded = cv2.imencode(".jpg", cleaned, [int(cv2.IMWRITE_JPEG_QUALITY), int(args.jpeg_quality)])
    if not ok:
        raise RuntimeError(f"Cannot encode cleaned worker page {page_index + 1}")
    encoded.tofile(str(output_jpg))
    del bgr, cleaned, encoded
    gc.collect()
    return width, height

def _process_pdf_sequential(
    input_pdf: Path,
    output_pdf: Path,
    args: argparse.Namespace,
    save_pages_dir: Path | None,
    debug_dir: Path | None,
) -> list[tuple[float, float, Path]]:
    """Sequential page processor, kept for debug mode and lowest memory usage."""
    page_records: list[tuple[float, float, Path]] = []
    tmp_dir = Path(args._tmp_dir)
    src = fitz.open(str(input_pdf))
    try:
        page_count = len(src)
        print(f"Pages: {page_count}")
        for i in range(page_count):
            print(f"Processing page {i + 1}/{page_count} ...")
            page = src.load_page(i)
            width_pt = float(page.rect.width)
            height_pt = float(page.rect.height)
            bgr = render_page_to_bgr(page, int(args.dpi))
            cleaned, debug = clean_page_bgr(bgr, args)

            page_img = tmp_dir / f"page_{i + 1:04d}.jpg"
            ok, encoded = cv2.imencode(".jpg", cleaned, [int(cv2.IMWRITE_JPEG_QUALITY), int(args.jpeg_quality)])
            if not ok:
                raise RuntimeError(f"Cannot encode cleaned page {i + 1}")
            encoded.tofile(str(page_img))
            page_records.append((width_pt, height_pt, page_img))

            if save_pages_dir:
                (save_pages_dir / f"page_{i + 1:04d}.jpg").write_bytes(page_img.read_bytes())
            if debug_dir:
                page_debug_dir = debug_dir / f"page_{i + 1:04d}"
                page_debug_dir.mkdir(parents=True, exist_ok=True)
                for name, mask in debug.items():
                    if isinstance(mask, np.ndarray):
                        imwrite_unicode(page_debug_dir / f"{name}.png", mask)

            del bgr, cleaned, encoded, debug
            gc.collect()
    finally:
        src.close()
    return page_records


def _auto_jobs(args: argparse.Namespace, page_count: int, debug_dir: Path | None) -> int:
    """Choose a conservative worker count; 300-DPI pages use a lot of RAM."""
    requested = int(getattr(args, "jobs", 0))
    if debug_dir is not None:
        return 1
    if requested == 1:
        return 1
    if requested > 1:
        return min(requested, max(1, page_count))
    cpu = os.cpu_count() or 2
    # Two workers is usually the best safe default for 300-DPI document pages:
    # good speedup without the memory spikes of 4+ concurrent OpenCV pipelines.
    return max(1, min(2, cpu - 1, page_count))


def _process_pdf_parallel(
    input_pdf: Path,
    args: argparse.Namespace,
    page_count: int,
    jobs: int,
    save_pages_dir: Path | None,
) -> list[tuple[float, float, Path]]:
    """Parallel page processing into temporary JPGs."""
    tmp_dir = Path(args._tmp_dir)
    page_records: list[tuple[float, float, Path] | None] = [None] * page_count
    worker_args = argparse.Namespace(**vars(args))
    worker_args.save_pages = None
    worker_args.debug = None
    worker_args._need_debug = False

    print(f"Pages: {page_count}")
    print(f"Parallel workers: {jobs}")
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as ex:
        futures = {}
        for i in range(page_count):
            page_img = tmp_dir / f"page_{i + 1:04d}.jpg"
            fut = ex.submit(process_single_page_to_jpg, input_pdf, i, page_img, worker_args)
            futures[fut] = (i, page_img)

        done_count = 0
        for fut in concurrent.futures.as_completed(futures):
            i, page_img = futures[fut]
            width_pt, height_pt = fut.result()
            page_records[i] = (width_pt, height_pt, page_img)
            done_count += 1
            print(f"Processed page {i + 1}/{page_count} ({done_count}/{page_count} done)")
            if save_pages_dir:
                (save_pages_dir / f"page_{i + 1:04d}.jpg").write_bytes(page_img.read_bytes())

    return [rec for rec in page_records if rec is not None]


def process_pdf(input_pdf: Path, output_pdf: Path, args: argparse.Namespace) -> None:
    """Disk-backed PDF processor with optional page-level parallelism."""
    input_pdf = input_pdf.expanduser().resolve()
    output_pdf = output_pdf.expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    save_pages_dir = Path(args.save_pages) if args.save_pages else None
    debug_dir = Path(args.debug) if args.debug else None
    if save_pages_dir:
        save_pages_dir.mkdir(parents=True, exist_ok=True)
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
    setattr(args, "_need_debug", bool(debug_dir))

    print(f"Input: {input_pdf}")
    print(f"Output: {output_pdf}")
    print(
        "Settings: "
        f"dpi={args.dpi}, safe_mode=ON, "
        f"diag_gray={args.diag_gray_low}-{args.diag_gray_high}, sat<={args.diag_sat_max}, "
        f"corridor_sat<={args.corridor_sat_max}, local_bg_kernel={args.local_bg_kernel}, "
        f"line_restore={args.line_restore}, tight_roi=ON, color_panel_preserve={args.color_panel_preserve}, "
        f"color_panel_mode=local_patch, jobs={getattr(args, 'jobs', 0)}"
    )

    with tempfile.TemporaryDirectory(prefix="tdm_v696_pages_") as tmp:
        args._tmp_dir = tmp
        src = fitz.open(str(input_pdf))
        try:
            page_count = len(src)
        finally:
            src.close()

        jobs = _auto_jobs(args, page_count, debug_dir)
        if jobs > 1:
            page_records = _process_pdf_parallel(input_pdf, args, page_count, jobs, save_pages_dir)
        else:
            page_records = _process_pdf_sequential(input_pdf, output_pdf, args, save_pages_dir, debug_dir)

        print("Building PDF ...")
        if not page_records:
            raise RuntimeError("No pages were processed")
        out_doc = fitz.open()
        try:
            for width_pt, height_pt, img_path in page_records:
                page = out_doc.new_page(width=width_pt, height=height_pt)
                page.insert_image(page.rect, stream=img_path.read_bytes(), keep_proportion=False)
            out_doc.save(str(output_pdf), garbage=4, deflate=True)
            print("Done.")
        finally:
            out_doc.close()
            gc.collect()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="V7.6.5 safe apply-last global ruled-line reconstruction + ROI-cache tight-ROI safe diagonal cleaner.")
    p.add_argument("input_pdf", type=Path)
    p.add_argument("-o", "--output", type=Path, default=None)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--image-format", choices=["jpg", "png"], default="jpg")
    p.add_argument("--jpeg-quality", type=int, default=95)
    p.add_argument("--jobs", type=int, default=0, help="Number of parallel page workers. 0 = auto, 1 = sequential.")
    p.add_argument("--mode", choices=["balanced", "quality"], default="balanced",
                   help="balanced = conditional fast modules; quality = all safeguards enabled.")

    # Header/footer cleanup.
    p.add_argument("--header-footer-gray-low", type=int, default=90)
    p.add_argument("--header-footer-gray-high", type=int, default=253)
    p.add_argument("--header-footer-sat-max", type=int, default=95)
    p.add_argument("--header-footer-protect-gray", type=int, default=85)
    p.add_argument("--header-footer-protect-sat", type=int, default=95)

    # Diagonal corridor geometry.
    p.add_argument("--diag-band-width-px", type=int, default=0, help="0 = auto-scale from rendered page width")
    p.add_argument("--diag-x-min", type=float, default=0.50)
    p.add_argument("--diag-x-max", type=float, default=0.998)
    p.add_argument("--diag-y-min", type=float, default=0.48)
    p.add_argument("--diag-y-max", type=float, default=0.998)

    # Diagonal watermark/corridor thresholds.
    p.add_argument("--diag-gray-low", type=int, default=70)
    p.add_argument("--diag-gray-high", type=int, default=254)
    p.add_argument("--diag-sat-max", type=int, default=155)
    p.add_argument("--diag-min-bg-diff", type=int, default=1)
    p.add_argument("--corridor-gray-low", type=int, default=115)
    p.add_argument("--corridor-gray-high", type=int, default=254)
    p.add_argument("--corridor-sat-max", type=int, default=210)
    p.add_argument("--corridor-bg-gray-min", type=int, default=145)

    # Halo catch for almost-white watermark on paper.
    p.add_argument("--diag-halo-gray-low", type=int, default=200)
    p.add_argument("--diag-halo-gray-high", type=int, default=253)
    p.add_argument("--diag-halo-sat-max", type=int, default=95)
    p.add_argument("--diag-halo-bg-gray-min", type=int, default=225)
    p.add_argument("--diag-halo-bg-sat-max", type=int, default=95)
    p.add_argument("--diag-halo-min-bg-diff", type=int, default=1)

    p.add_argument("--diag-close-kernel", type=int, default=5)
    p.add_argument("--diag-dilate", type=int, default=0)

    # Local/component background reconstruction.
    p.add_argument("--local-bg-kernel", type=int, default=101)
    p.add_argument("--replace-blend-alpha", type=float, default=1.0)
    p.add_argument("--use-inpaint-before-background", action="store_true", default=False)
    p.add_argument("--inpaint-radius", type=float, default=2.0)

    # White reconstruction.
    p.add_argument("--force-neutral-white", dest="force_neutral_white", action="store_true", default=True)
    p.add_argument("--no-force-neutral-white", dest="force_neutral_white", action="store_false")
    p.add_argument("--white-local-gray-min", type=int, default=236)
    p.add_argument("--white-local-sat-max", type=int, default=55)
    p.add_argument("--white-local-spread-max", type=int, default=12)

    # Pale color component detection.
    p.add_argument("--pale-color-gray-min", type=int, default=150)
    p.add_argument("--pale-color-value-min", type=int, default=150)
    p.add_argument("--pale-color-sat-min", type=int, default=18)
    p.add_argument("--pale-color-sat-max", type=int, default=180)
    p.add_argument("--pale-color-spread-min", type=int, default=7)
    p.add_argument("--pale-color-component-min-area", type=int, default=120)
    p.add_argument("--pale-color-component-max-area", type=int, default=200000)
    p.add_argument("--pale-color-component-dilate", type=int, default=11)
    p.add_argument("--pale-color-min-clean-pixels", type=int, default=25)

    # Content protection.
    p.add_argument("--safe-text-gray-max", type=int, default=92)
    p.add_argument("--safe-color-ink-sat-min", type=int, default=60)
    p.add_argument("--safe-color-ink-gray-max", type=int, default=205)
    p.add_argument("--safe-color-ink-value-max", type=int, default=225)
    p.add_argument("--edge-canny-low", type=int, default=28)
    p.add_argument("--edge-canny-high", type=int, default=105)
    p.add_argument("--edge-dark-gray-max", type=int, default=82)
    p.add_argument("--edge-color-gray-max", type=int, default=165)
    p.add_argument("--edge-protect-gray-max", type=int, default=200, help="Kept for backwards compatibility; edge_dark_gray_max/edge_color_gray_max are used.")
    p.add_argument("--edge-protect-sat-min", type=int, default=45)
    p.add_argument("--safe-protect-dilate", type=int, default=1)
    p.add_argument("--safe-text-aa-enable", dest="safe_text_aa_enable", action="store_true", default=True)
    p.add_argument("--no-safe-text-aa", dest="safe_text_aa_enable", action="store_false")
    p.add_argument("--safe-text-aa-gray-max", type=int, default=145)
    p.add_argument("--safe-text-aa-min-contrast", type=int, default=42)
    p.add_argument("--safe-text-aa-local-kernel", type=int, default=31)
    p.add_argument("--safe-text-aa-component-min-area", type=int, default=2)
    p.add_argument("--safe-text-aa-component-max-area", type=int, default=25000)
    p.add_argument("--safe-text-aa-dilate", type=int, default=1)


    # V6 ruled-paper line restoration.
    p.add_argument("--line-restore", dest="line_restore", action="store_true", default=True)
    p.add_argument("--no-line-restore", dest="line_restore", action="store_false")
    p.add_argument("--line-detect-x-min", type=float, default=0.07)
    p.add_argument("--line-detect-x-max", type=float, default=0.94)
    p.add_argument("--line-detect-y-min", type=float, default=0.10)
    p.add_argument("--line-detect-y-max", type=float, default=0.92)
    p.add_argument("--line-restore-x-min", type=float, default=0.055)
    p.add_argument("--line-restore-x-max", type=float, default=0.94)
    p.add_argument("--line-restore-y-min", type=float, default=0.05)
    p.add_argument("--line-restore-y-max", type=float, default=0.920)
    p.add_argument("--line-detect-dark-exclude-gray", type=int, default=190)
    p.add_argument("--line-row-baseline-kernel", type=int, default=31)
    p.add_argument("--line-phase-search-radius", type=int, default=2)
    p.add_argument("--line-center-refine-radius", type=int, default=3)
    p.add_argument("--line-spacing-min-px", type=int, default=0, help="0 = auto from page height")
    p.add_argument("--line-spacing-max-px", type=int, default=0, help="0 = auto from page height")
    p.add_argument("--line-min-periodic-score", type=float, default=0.03)
    p.add_argument("--line-min-centers", type=int, default=8)
    p.add_argument("--line-damage-dilate-x", type=int, default=120)
    p.add_argument("--line-damage-dilate-y", type=int, default=3)
    p.add_argument("--line-paper-gray-min", type=int, default=165)
    p.add_argument("--line-paper-gray-max", type=int, default=254)
    p.add_argument("--line-paper-sat-max", type=int, default=95)
    p.add_argument("--line-paper-spread-max", type=int, default=35)
    p.add_argument("--line-min-sample-pixels", type=int, default=100)
    p.add_argument("--line-too-white-gray", type=int, default=248)
    p.add_argument("--line-low-percentile", type=float, default=20.0)
    p.add_argument("--line-alpha-center", type=float, default=0.95)
    p.add_argument("--line-alpha-edge", type=float, default=0.16)
    p.add_argument("--line-fallback-bgr", nargs=3, type=int, default=[219, 217, 233])
    p.add_argument("--line-restore-damaged-only", dest="line_restore_damaged_only", action="store_true", default=True,
                   help="Only redraw ruled lines where watermark cleanup actually changed pixels.")
    p.add_argument("--line-restore-global", dest="line_restore_damaged_only", action="store_false",
                   help="Legacy behavior: redraw ruled-line template globally on paper-like pixels.")
    p.add_argument("--line-restore-damage-gate-dilate-x", type=int, default=72)
    p.add_argument("--line-restore-damage-gate-dilate-y", type=int, default=3)
    p.add_argument("--line-restore-limit-to-diag-band", dest="line_restore_limit_to_diag_band", action="store_true", default=True)
    p.add_argument("--no-line-restore-limit-to-diag-band", dest="line_restore_limit_to_diag_band", action="store_false")
    p.add_argument("--line-evidence-baseline-kernel", type=int, default=21)
    p.add_argument("--line-evidence-min-contrast", type=int, default=2)
    p.add_argument("--line-evidence-close-x", type=int, default=5)
    p.add_argument("--line-evidence-y-radius", type=int, default=1)
    p.add_argument("--line-evidence-min-row-coverage", type=float, default=0.18)
    p.add_argument("--line-gap-bridge", dest="line_gap_bridge", action="store_true", default=True,
                   help="Bridge broken ruled lines inside watermark/damage areas without global overdraw.")
    p.add_argument("--no-line-gap-bridge", dest="line_gap_bridge", action="store_false")
    p.add_argument("--line-gap-bridge-close-x", type=int, default=260)
    p.add_argument("--line-gap-bridge-damage-dilate-x", type=int, default=190)
    p.add_argument("--line-gap-bridge-y-radius", type=int, default=1)
    p.add_argument("--line-gap-bridge-min-clean-pixels", type=int, default=18)
    p.add_argument("--line-gap-bridge-gray-min", type=int, default=168)
    p.add_argument("--line-gap-bridge-sat-max", type=int, default=135)
    p.add_argument("--line-gap-bridge-spread-max", type=int, default=70)
    p.add_argument("--line-gap-bridge-text-gray-max", type=int, default=178)
    p.add_argument("--line-gap-bridge-text-dilate-x", type=int, default=9)

    # V7.6.3 global ruled-line reconstruction with content-aware clipping.
    p.add_argument("--line-global-reconstruction", dest="line_global_reconstruction", action="store_true", default=True,
                   help="Rebuild a logical ruled-line layer from the detected grid, clipped by content guards.")
    p.add_argument("--no-line-global-reconstruction", dest="line_global_reconstruction", action="store_false")
    p.add_argument("--line-restore-scope", default="watermark_roi_only",
                   choices=["watermark_roi_only", "full_page"],
                   help="V7.6.6: restore ruled lines only inside watermark/damage ROI by default; full_page is opt-in legacy behavior.")
    p.add_argument("--line-global-full-page", dest="line_global_full_page", action="store_true", default=False,
                   help="Opt-in legacy mode: allow synthesis on all blank ruled-paper areas; default is watermark ROI only.")
    p.add_argument("--line-global-diagonal-only", dest="line_global_full_page", action="store_false",
                   help="Restrict global synthesis to diagonal/damage neighborhoods.")
    p.add_argument("--line-global-y-radius", type=int, default=1)
    p.add_argument("--line-global-blank-gray-min", type=int, default=162)
    p.add_argument("--line-global-blank-sat-max", type=int, default=150)
    p.add_argument("--line-global-blank-spread-max", type=int, default=80)
    p.add_argument("--line-global-missing-gray-min", type=int, default=236)
    p.add_argument("--line-global-missing-sat-max", type=int, default=150)
    p.add_argument("--line-global-missing-spread-max", type=int, default=85)
    p.add_argument("--line-global-damage-dilate-x", type=int, default=420)
    p.add_argument("--line-global-damage-dilate-y", type=int, default=5)
    p.add_argument("--line-global-text-gray-max", type=int, default=188)
    p.add_argument("--line-global-colored-ink-sat-min", type=int, default=36)
    p.add_argument("--line-global-colored-ink-gray-max", type=int, default=238)
    p.add_argument("--line-global-edge-canny-low", type=int, default=24)
    p.add_argument("--line-global-edge-canny-high", type=int, default=96)
    p.add_argument("--line-global-edge-gray-max", type=int, default=160)
    p.add_argument("--line-global-fill-gray-min", type=int, default=135)
    p.add_argument("--line-global-fill-sat-min", type=int, default=12)
    p.add_argument("--line-global-fill-spread-min", type=int, default=5)
    p.add_argument("--line-global-fill-close-x", type=int, default=11)
    p.add_argument("--line-global-fill-close-y", type=int, default=7)
    p.add_argument("--line-global-guard-dilate-x", type=int, default=5)
    p.add_argument("--line-global-guard-dilate-y", type=int, default=3)
    p.add_argument("--line-global-guard-min-area", type=int, default=2)
    p.add_argument("--line-global-min-run-px", type=int, default=2)
    p.add_argument("--line-global-min-confidence", type=float, default=0.54,
                   help="Minimum detected ruled-grid confidence required before any global synthesis.")
    p.add_argument("--line-global-aggressive-confidence", type=float, default=0.70,
                   help="Confidence required before full-page global synthesis; lower confidence uses damage-near synthesis only.")

    # V7.6.7: clean watermark ROI background before restoring ruled lines.
    p.add_argument("--watermark-roi-background-reconstruct", dest="watermark_roi_background_reconstruct",
                   action="store_true", default=True,
                   help="Clean the diagonal watermark ROI background before ruled-line restoration.")
    p.add_argument("--no-watermark-roi-background-reconstruct", dest="watermark_roi_background_reconstruct", action="store_false")
    p.add_argument("--watermark-roi-bg-full-band", dest="watermark_roi_bg_full_band", action="store_true", default=True,
                   help="Clean paper-like background across the diagonal watermark band before restoring lines.")
    p.add_argument("--no-watermark-roi-bg-full-band", dest="watermark_roi_bg_full_band", action="store_false")
    p.add_argument("--watermark-roi-bg-expand-x", type=int, default=26,
                   help="Horizontal dilation for the diagonal watermark ROI background replacement mask.")
    p.add_argument("--watermark-roi-bg-expand-y", type=int, default=7,
                   help="Vertical dilation for the diagonal watermark ROI background replacement mask.")
    p.add_argument("--watermark-roi-bg-line-erase-y-radius", type=int, default=1,
                   help="Rows around detected ruled-line centers to clean inside the watermark ROI before redrawing.")
    p.add_argument("--watermark-roi-bg-content-dilate-x", type=int, default=5,
                   help="Extra X dilation for content guard while cleaning the watermark ROI background.")
    p.add_argument("--watermark-roi-bg-content-dilate-y", type=int, default=3,
                   help="Extra Y dilation for content guard while cleaning the watermark ROI background.")
    p.add_argument("--watermark-roi-bg-content-gray-max", type=int, default=155,
                   help="Protect cleaned-page dark strokes up to this gray while cleaning watermark ROI.")
    p.add_argument("--watermark-roi-bg-content-sat-min", type=int, default=42,
                   help="Protect colored graph/content strokes above this saturation while cleaning watermark ROI.")
    p.add_argument("--watermark-roi-bg-gray-min", type=int, default=176,
                   help="Only clean reasonably light background pixels in the watermark ROI.")
    p.add_argument("--watermark-roi-bg-sat-max", type=int, default=160)
    p.add_argument("--watermark-roi-bg-spread-max", type=int, default=95)
    p.add_argument("--watermark-roi-bg-alpha", type=float, default=1.0,
                   help="Replacement strength for clean background reconstruction inside watermark ROI.")
    p.add_argument("--watermark-roi-bg-use-inpaint", dest="watermark_roi_bg_use_inpaint", action="store_true", default=False)
    p.add_argument("--no-watermark-roi-bg-use-inpaint", dest="watermark_roi_bg_use_inpaint", action="store_false")

    # V6.4 footer-safe line template rebuild.
    p.add_argument("--footer-detect-x-min", type=float, default=0.04)
    p.add_argument("--footer-detect-x-max", type=float, default=0.96)
    p.add_argument("--footer-detect-y-min", type=float, default=0.82)
    p.add_argument("--footer-detect-y-max", type=float, default=0.995)
    p.add_argument("--footer-rule-gray-min", type=int, default=95)
    p.add_argument("--footer-rule-gray-max", type=int, default=235)
    p.add_argument("--footer-rule-sat-min", type=int, default=12)
    p.add_argument("--footer-rule-spread-min", type=int, default=4)
    p.add_argument("--footer-rule-min-coverage", type=float, default=0.55)
    p.add_argument("--footer-guard-px", type=int, default=32)
    p.add_argument("--footer-template-zone-px", type=int, default=330)
    p.add_argument("--footer-template-spacing-px", type=int, default=0, help="0 = use detected line spacing")
    p.add_argument("--footer-template-extend-to-border", action="store_true", default=True)
    p.add_argument("--no-footer-template-extend-to-border", dest="footer_template_extend_to_border", action="store_false")
    p.add_argument("--footer-line-draw-gray-min", type=int, default=178)
    p.add_argument("--footer-line-draw-sat-max", type=int, default=150)
    p.add_argument("--footer-line-draw-spread-max", type=int, default=80)
    p.add_argument("--footer-line-alpha-center", type=float, default=0.72)
    p.add_argument("--footer-line-alpha-edge", type=float, default=0.22)

    # V6.7 header-right text protection.
    p.add_argument("--header-right-protect", dest="header_right_protect", action="store_true", default=True)
    p.add_argument("--no-header-right-protect", dest="header_right_protect", action="store_false")
    p.add_argument("--header-right-protect-x-min", type=float, default=0.635)
    p.add_argument("--header-right-protect-x-max", type=float, default=0.995)
    p.add_argument("--header-right-protect-y-min", type=float, default=0.000)
    p.add_argument("--header-right-protect-y-max", type=float, default=0.048)
    p.add_argument("--header-right-protect-gray-max", type=int, default=225)
    p.add_argument("--header-right-protect-gray-strict", type=int, default=205)
    p.add_argument("--header-right-protect-sat-min", type=int, default=18)
    p.add_argument("--header-right-protect-spread-min", type=int, default=6)
    p.add_argument("--header-right-protect-dilate-x", type=int, default=17)
    p.add_argument("--header-right-protect-dilate-y", type=int, default=3)



    # V6.8 color panel geometry preservation.
    p.add_argument("--color-panel-preserve", dest="color_panel_preserve", action="store_true", default=True)
    p.add_argument("--no-color-panel-preserve", dest="color_panel_preserve", action="store_false")
    p.add_argument("--color-panel-hue-min", type=int, default=70)
    p.add_argument("--color-panel-hue-max", type=int, default=105)
    p.add_argument("--color-panel-sat-min", type=int, default=10)
    p.add_argument("--color-panel-sat-max", type=int, default=115)
    p.add_argument("--color-panel-extra-sat-min", type=int, default=16)
    p.add_argument("--color-panel-extra-sat-max", type=int, default=115)
    p.add_argument("--color-panel-value-min", type=int, default=165)
    p.add_argument("--color-panel-gray-min", type=int, default=155)
    p.add_argument("--color-panel-spread-min", type=int, default=6)
    p.add_argument("--color-panel-close-x", type=int, default=55)
    p.add_argument("--color-panel-close-y", type=int, default=9)
    p.add_argument("--color-panel-min-area", type=int, default=1800)
    p.add_argument("--color-panel-min-width-px", type=int, default=180)
    p.add_argument("--color-panel-min-width-ratio", type=float, default=0.12)
    p.add_argument("--color-panel-min-height-px", type=int, default=18)
    p.add_argument("--color-panel-max-height-ratio", type=float, default=0.16)
    p.add_argument("--color-panel-min-coverage", type=float, default=0.38)
    p.add_argument("--color-panel-min-aspect", type=float, default=2.2)
    p.add_argument("--color-panel-expand-x", type=int, default=1)
    p.add_argument("--color-panel-expand-y", type=int, default=2)
    p.add_argument("--color-panel-min-sample-pixels", type=int, default=150)
    p.add_argument("--color-panel-min-color-spread", type=float, default=8.0)
    p.add_argument("--color-panel-rebuild-gray-min", type=int, default=120)
    p.add_argument("--color-panel-alpha", type=float, default=1.0)
    # V6.9 conditional panel mode: guard panels from force-white, patch local watermark
    # pixels only, rebuild full panel only if visible white-hole damage is detected.
    p.add_argument("--color-panel-protect-from-corridor", dest="color_panel_protect_from_corridor", action="store_true", default=True)
    p.add_argument("--no-color-panel-protect-from-corridor", dest="color_panel_protect_from_corridor", action="store_false")
    p.add_argument("--color-panel-rebuild-fallback", dest="color_panel_rebuild_fallback", action="store_true", default=False)
    p.add_argument("--no-color-panel-rebuild-fallback", dest="color_panel_rebuild_fallback", action="store_false")
    p.add_argument("--color-panel-patch-alpha", type=float, default=0.92)
    p.add_argument("--color-panel-local-patch", dest="color_panel_local_patch", action="store_true", default=True)
    p.add_argument("--no-color-panel-local-patch", dest="color_panel_local_patch", action="store_false")
    p.add_argument("--color-panel-local-patch-dilate-x", type=int, default=9)
    p.add_argument("--color-panel-local-patch-dilate-y", type=int, default=3)
    p.add_argument("--color-panel-overflow-trim", dest="color_panel_overflow_trim", action="store_true", default=True)
    p.add_argument("--no-color-panel-overflow-trim", dest="color_panel_overflow_trim", action="store_false")
    p.add_argument("--color-panel-overflow-safe-dilate-x", type=int, default=5)
    p.add_argument("--color-panel-overflow-safe-dilate-y", type=int, default=3)
    p.add_argument("--color-panel-overflow-near-dilate-x", type=int, default=55)
    p.add_argument("--color-panel-overflow-near-dilate-y", type=int, default=13)
    p.add_argument("--color-panel-overflow-white", nargs=3, type=int, default=[255, 255, 255])
    p.add_argument("--color-panel-outside-strip-clean", dest="color_panel_outside_strip_clean", action="store_true", default=True)
    p.add_argument("--no-color-panel-outside-strip-clean", dest="color_panel_outside_strip_clean", action="store_false")
    p.add_argument("--color-panel-outside-strip-near-dilate-x", type=int, default=41)
    p.add_argument("--color-panel-outside-strip-near-dilate-y", type=int, default=11)
    p.add_argument("--color-panel-outside-strip-safe-dilate-x", type=int, default=3)
    p.add_argument("--color-panel-outside-strip-safe-dilate-y", type=int, default=3)
    p.add_argument("--color-panel-outside-strip-gray-min", type=int, default=208)
    p.add_argument("--color-panel-outside-strip-sat-max", type=int, default=70)
    p.add_argument("--color-panel-outside-strip-spread-max", type=int, default=42)
    p.add_argument("--color-panel-outside-strip-close-x", type=int, default=13)
    p.add_argument("--color-panel-outside-strip-close-y", type=int, default=5)
    p.add_argument("--color-panel-hole-gray-min", type=int, default=247)
    p.add_argument("--color-panel-hole-sat-max", type=int, default=12)
    p.add_argument("--color-panel-hole-spread-max", type=int, default=10)
    p.add_argument("--color-panel-hole-ratio-trigger", type=float, default=0.028)
    p.add_argument("--color-panel-edge-hole-ratio-trigger", type=float, default=0.080)
    p.add_argument("--color-panel-edge-check-px", type=int, default=6)
    p.add_argument("--color-panel-edge-canny-low", type=int, default=25)
    p.add_argument("--color-panel-edge-canny-high", type=int, default=105)
    p.add_argument("--color-panel-edge-dark-gray-max", type=int, default=135)
    p.add_argument("--color-panel-edge-color-sat-min", type=int, default=55)
    p.add_argument("--color-panel-edge-color-gray-max", type=int, default=225)

    # V7.7.6 colored-panel watermark cleanup.  Runs only on detected flat pale
    # panels inside the diagonal watermark band; real text/borders/images are guarded.
    p.add_argument("--colored-panel-watermark-cleanup", dest="colored_panel_watermark_cleanup", action="store_true", default=False)
    p.add_argument("--no-colored-panel-watermark-cleanup", dest="colored_panel_watermark_cleanup", action="store_false")
    p.add_argument("--colored-panel-wm-min-band-pixels", type=int, default=8)
    p.add_argument("--colored-panel-wm-min-flat-sample", type=int, default=180)
    p.add_argument("--colored-panel-wm-flat-std-max", type=float, default=22.0)
    p.add_argument("--colored-panel-wm-flat-delta-p95-max", type=float, default=46.0)
    p.add_argument("--colored-panel-wm-real-text-gray-max", type=int, default=112)
    p.add_argument("--colored-panel-wm-real-edge-gray-max", type=int, default=132)
    p.add_argument("--colored-panel-wm-edge-canny-low", type=int, default=28)
    p.add_argument("--colored-panel-wm-edge-canny-high", type=int, default=105)
    p.add_argument("--colored-panel-wm-content-guard-dilate-x", type=int, default=3)
    p.add_argument("--colored-panel-wm-content-guard-dilate-y", type=int, default=2)
    p.add_argument("--colored-panel-wm-original-gray-min", type=int, default=118)
    p.add_argument("--colored-panel-wm-gray-min", type=int, default=118)
    p.add_argument("--colored-panel-wm-gray-max", type=int, default=252)
    p.add_argument("--colored-panel-wm-sat-max", type=int, default=135)
    p.add_argument("--colored-panel-wm-spread-max", type=int, default=105)
    p.add_argument("--colored-panel-wm-min-delta", type=int, default=9)
    p.add_argument("--colored-panel-wm-min-darkening", type=int, default=3)
    p.add_argument("--colored-panel-wm-min-pixels", type=int, default=6)
    p.add_argument("--colored-panel-wm-close-x", type=int, default=5)
    p.add_argument("--colored-panel-wm-close-y", type=int, default=3)
    p.add_argument("--colored-panel-wm-dilate-x", type=int, default=5)
    p.add_argument("--colored-panel-wm-dilate-y", type=int, default=3)
    p.add_argument("--colored-panel-wm-alpha", type=float, default=1.0)

    # V7.6.8 table-aware ROI cleanup for colored Đ/S answer tables.
    p.add_argument("--table-aware-cleanup", dest="table_aware_cleanup", action="store_true", default=True)
    p.add_argument("--no-table-aware-cleanup", dest="table_aware_cleanup", action="store_false")
    p.add_argument("--table-cell-gray-min", type=int, default=165)
    p.add_argument("--table-cell-value-min", type=int, default=168)
    p.add_argument("--table-cell-sat-min", type=int, default=8)
    p.add_argument("--table-cell-sat-max", type=int, default=135)
    p.add_argument("--table-cell-spread-min", type=int, default=5)
    p.add_argument("--table-cell-min-area", type=int, default=250)
    p.add_argument("--table-cell-min-width-px", type=int, default=12)
    p.add_argument("--table-cell-min-height-px", type=int, default=12)
    p.add_argument("--table-cell-max-width-ratio", type=float, default=0.24)
    p.add_argument("--table-cell-max-height-ratio", type=float, default=0.40)
    p.add_argument("--table-cell-min-coverage", type=float, default=0.22)
    p.add_argument("--table-cell-min-sample-pixels", type=int, default=40)
    p.add_argument("--table-cell-min-band-pixels", type=int, default=6)
    p.add_argument("--table-cell-min-color-spread", type=float, default=5.0)
    p.add_argument("--table-band-intersect-dilate-x", type=int, default=13)
    p.add_argument("--table-band-intersect-dilate-y", type=int, default=7)
    p.add_argument("--table-near-dilate-x", type=int, default=18)
    p.add_argument("--table-near-dilate-y", type=int, default=10)
    p.add_argument("--table-bg-patch-alpha", type=float, default=1.0)
    p.add_argument("--table-bg-patch-gray-min", type=int, default=155)
    p.add_argument("--table-bg-patch-sat-max", type=int, default=180)
    p.add_argument("--table-bg-patch-spread-max", type=int, default=120)
    p.add_argument("--table-bg-patch-diag-dilate-x", type=int, default=13)
    p.add_argument("--table-bg-patch-diag-dilate-y", type=int, default=5)
    p.add_argument("--table-stroke-gray-max", type=int, default=185)
    p.add_argument("--table-stroke-sat-min", type=int, default=70)
    p.add_argument("--table-stroke-color-gray-max", type=int, default=225)
    p.add_argument("--table-border-min-len-x", type=int, default=28)
    p.add_argument("--table-border-min-len-y", type=int, default=28)
    p.add_argument("--table-circle-canny-low", type=int, default=35)
    p.add_argument("--table-circle-canny-high", type=int, default=120)
    p.add_argument("--table-compact-stroke-min-area", type=int, default=8)
    p.add_argument("--table-compact-stroke-max-area", type=int, default=2200)
    p.add_argument("--table-compact-stroke-max-w", type=int, default=95)
    p.add_argument("--table-compact-stroke-max-h", type=int, default=95)
    p.add_argument("--table-restore-stroke-dilate", dest="table_restore_stroke_dilate", action="store_true", default=True)
    p.add_argument("--no-table-restore-stroke-dilate", dest="table_restore_stroke_dilate", action="store_false")

    # V7.6.9 graph-stroke restore, safe and watermark-ROI-only.
    p.add_argument("--graph-restore-enabled", dest="graph_restore_enabled", action="store_true", default=True)
    p.add_argument("--no-graph-restore", dest="graph_restore_enabled", action="store_false")
    p.add_argument("--graph-roi-dilate-x", type=int, default=6)
    p.add_argument("--graph-roi-dilate-y", type=int, default=3)
    p.add_argument("--graph-restore-min-roi-pixels", type=int, default=40)
    p.add_argument("--graph-restore-min-pixels", type=int, default=4)
    p.add_argument("--graph-restore-min-evidence-pixels", type=int, default=12)
    p.add_argument("--graph-restore-min-confidence", type=float, default=0.05)
    p.add_argument("--graph-restore-dilate", dest="graph_restore_dilate", action="store_true", default=True)
    p.add_argument("--no-graph-restore-dilate", dest="graph_restore_dilate", action="store_false")
    p.add_argument("--graph-table-exclude-dilate-x", type=int, default=24)
    p.add_argument("--graph-table-exclude-dilate-y", type=int, default=16)
    p.add_argument("--graph-table-border-min-len-x", type=int, default=34)
    p.add_argument("--graph-table-border-min-len-y", type=int, default=34)
    p.add_argument("--graph-black-gray-max", type=int, default=120)
    p.add_argument("--graph-black-aa-gray-max", type=int, default=170)
    p.add_argument("--graph-black-weaken-diff", type=int, default=10)
    p.add_argument("--graph-edge-canny-low", type=int, default=24)
    p.add_argument("--graph-edge-canny-high", type=int, default=105)
    p.add_argument("--graph-line-min-len", type=int, default=28)
    p.add_argument("--graph-dashed-close-px", type=int, default=17)
    p.add_argument("--graph-blue-hue-min", type=int, default=88)
    p.add_argument("--graph-blue-hue-max", type=int, default=142)
    p.add_argument("--graph-blue-sat-min", type=int, default=35)
    p.add_argument("--graph-blue-sat-min-alt", type=int, default=25)
    p.add_argument("--graph-blue-sat-min-clean", type=int, default=25)
    p.add_argument("--graph-blue-gray-max", type=int, default=245)
    p.add_argument("--graph-blue-b-minus-r-min", type=int, default=20)
    p.add_argument("--graph-blue-b-minus-g-min", type=int, default=5)
    p.add_argument("--graph-blue-min-component-area", type=int, default=8)
    p.add_argument("--graph-blue-min-component-span", type=int, default=9)
    p.add_argument("--graph-blue-weaken-diff", type=int, default=10)
    p.add_argument("--graph-blue-sat-loss", type=int, default=12)
    p.add_argument("--graph-restore-markers", dest="graph_restore_markers", action="store_true", default=True)
    p.add_argument("--no-graph-restore-markers", dest="graph_restore_markers", action="store_false")
    p.add_argument("--graph-marker-gray-max", type=int, default=130)
    p.add_argument("--graph-marker-min-area", type=int, default=3)
    p.add_argument("--graph-marker-max-area", type=int, default=180)

    # V7 mode gates.
    p.add_argument("--v7-panel-gate-min-pixels", type=int, default=120, help=argparse.SUPPRESS)
    p.add_argument("--v7-line-gate-min-rows", type=int, default=18, help=argparse.SUPPRESS)

    p.add_argument("--worker-timeout", type=float, default=75.0, help=argparse.SUPPRESS)
    p.add_argument("--worker-retries", type=int, default=2, help=argparse.SUPPRESS)
    p.add_argument("--worker-args-pkl", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--page-index", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--page-output", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--save-pages", type=str, default=None)
    p.add_argument("--debug", type=str, default=None)

    args = p.parse_args(argv)
    if args.output is None:
        args.output = args.input_pdf.with_name(args.input_pdf.stem + "_clean_v7_roi_cache_modes.pdf")
    return apply_v7_mode_presets(args)



# =====================================================================
# Drop-in image-level adapter for Final tool / processors.py
# =====================================================================
@dataclass
class ProcessingSettings:
    """
    Compatibility settings for the unified GUI.

    The old Final tool constructs ProcessingSettings with template-matching
    fields (top_template_path, diag_threshold, ...). V6.9.5 does not need
    templates, but we keep those fields so this file can replace the old
    `watermaker TDM.py` without breaking processors.py.

    Extra V6.9.5 tuning values can be supplied through config.json and are stored
    in `extra`.
    """
    top_template_path: str = "builtin"
    bottom_template_path: str = "builtin"
    diag_template_path: str = "builtin"
    top_threshold: float = 0.55
    bottom_threshold: float = 0.55
    diag_threshold: float = 0.25
    protect_gray_threshold: int = 105
    min_matched_regions: int = 1
    header_height: str = "0%"
    footer_height: str = "0%"
    output_grayscale: bool = False
    graph_safe_paper_mode: bool = True
    mode: str = "balanced"
    extra: dict[str, Any] = field(default_factory=dict)


# Only these config keys should be copied from config.json into the argparse
# namespace used internally by the V6.9.5 algorithm.
_V67_ARG_KEYS = {
    "mode", "v7_panel_gate_min_pixels", "v7_line_gate_min_rows",
    "header_footer_gray_low", "header_footer_gray_high", "header_footer_sat_max",
    "header_footer_protect_gray", "header_footer_protect_sat",
    "diag_band_width_px", "diag_x_min", "diag_x_max", "diag_y_min", "diag_y_max",
    "diag_gray_low", "diag_gray_high", "diag_sat_max", "diag_min_bg_diff",
    "corridor_gray_low", "corridor_gray_high", "corridor_sat_max", "corridor_bg_gray_min",
    "diag_halo_gray_low", "diag_halo_gray_high", "diag_halo_sat_max",
    "diag_halo_bg_gray_min", "diag_halo_bg_sat_max", "diag_halo_min_bg_diff",
    "diag_close_kernel", "diag_dilate", "local_bg_kernel", "replace_blend_alpha",
    "use_inpaint_before_background", "inpaint_radius", "force_neutral_white",
    "white_local_gray_min", "white_local_sat_max", "white_local_spread_max",
    "pale_color_gray_min", "pale_color_value_min", "pale_color_sat_min", "pale_color_sat_max",
    "pale_color_spread_min", "pale_color_component_min_area", "pale_color_component_max_area",
    "pale_color_component_dilate", "pale_color_min_clean_pixels",
    "safe_text_gray_max", "safe_text_aa_enable", "safe_text_aa_gray_max",
    "safe_text_aa_min_contrast", "safe_text_aa_local_kernel",
    "safe_text_aa_component_min_area", "safe_text_aa_component_max_area",
    "safe_text_aa_dilate", "safe_color_ink_sat_min", "safe_color_ink_gray_max",
    "safe_color_ink_value_max", "edge_canny_low", "edge_canny_high",
    "edge_dark_gray_max", "edge_color_gray_max", "edge_protect_sat_min",
    "safe_protect_dilate", "line_restore", "line_detect_x_min", "line_detect_x_max",
    "line_detect_y_min", "line_detect_y_max", "line_restore_x_min", "line_restore_x_max",
    "line_restore_y_min", "line_restore_y_max", "line_detect_dark_exclude_gray",
    "line_row_baseline_kernel", "line_phase_search_radius", "line_center_refine_radius",
    "line_spacing_min_px", "line_spacing_max_px", "line_min_periodic_score",
    "line_min_centers", "line_damage_dilate_x", "line_damage_dilate_y",
    "line_paper_gray_min", "line_paper_gray_max", "line_paper_sat_max",
    "line_paper_spread_max", "line_min_sample_pixels", "line_too_white_gray",
    "line_low_percentile", "line_alpha_center", "line_alpha_edge", "line_fallback_bgr",
    "line_restore_damaged_only", "line_restore_damage_gate_dilate_x", "line_restore_damage_gate_dilate_y",
    "line_restore_limit_to_diag_band", "line_evidence_baseline_kernel", "line_evidence_min_contrast",
    "line_evidence_close_x", "line_evidence_y_radius", "line_evidence_min_row_coverage",
    "line_gap_bridge", "line_gap_bridge_close_x", "line_gap_bridge_damage_dilate_x",
    "line_gap_bridge_y_radius", "line_gap_bridge_min_clean_pixels", "line_gap_bridge_gray_min",
    "line_gap_bridge_sat_max", "line_gap_bridge_spread_max", "line_gap_bridge_text_gray_max",
    "line_gap_bridge_text_dilate_x",
    "line_global_reconstruction", "line_global_full_page", "line_restore_scope", "line_global_y_radius",
    "line_global_blank_gray_min", "line_global_blank_sat_max", "line_global_blank_spread_max",
    "line_global_missing_gray_min", "line_global_missing_sat_max", "line_global_missing_spread_max",
    "line_global_damage_dilate_x", "line_global_damage_dilate_y", "line_global_text_gray_max",
    "line_global_colored_ink_sat_min", "line_global_colored_ink_gray_max",
    "line_global_edge_canny_low", "line_global_edge_canny_high", "line_global_edge_gray_max",
    "line_global_fill_gray_min", "line_global_fill_sat_min", "line_global_fill_spread_min",
    "line_global_fill_close_x", "line_global_fill_close_y", "line_global_guard_dilate_x",
    "line_global_guard_dilate_y", "line_global_guard_min_area", "line_global_min_run_px",
    "line_global_min_confidence", "line_global_aggressive_confidence",
    "watermark_roi_background_reconstruct", "watermark_roi_bg_full_band",
    "watermark_roi_bg_expand_x", "watermark_roi_bg_expand_y",
    "watermark_roi_bg_line_erase_y_radius", "watermark_roi_bg_content_dilate_x",
    "watermark_roi_bg_content_dilate_y", "watermark_roi_bg_content_gray_max",
    "watermark_roi_bg_content_sat_min", "watermark_roi_bg_gray_min",
    "watermark_roi_bg_sat_max", "watermark_roi_bg_spread_max", "watermark_roi_bg_alpha",
    "watermark_roi_bg_use_inpaint",
    "footer_detect_x_min", "footer_detect_x_max", "footer_detect_y_min", "footer_detect_y_max",
    "footer_rule_gray_min", "footer_rule_gray_max", "footer_rule_sat_min",
    "footer_rule_spread_min", "footer_rule_min_coverage", "footer_guard_px",
    "footer_template_zone_px", "footer_template_spacing_px", "footer_template_extend_to_border",
    "footer_line_draw_gray_min", "footer_line_draw_sat_max", "footer_line_draw_spread_max",
    "footer_line_alpha_center", "footer_line_alpha_edge", "header_right_protect",
    "header_right_protect_x_min", "header_right_protect_x_max",
    "header_right_protect_y_min", "header_right_protect_y_max",
    "header_right_protect_gray_max", "header_right_protect_gray_strict",
    "header_right_protect_sat_min", "header_right_protect_spread_min",
    "header_right_protect_dilate_x", "header_right_protect_dilate_y",
    "color_panel_preserve", "color_panel_hue_min", "color_panel_hue_max",
    "color_panel_sat_min", "color_panel_sat_max", "color_panel_extra_sat_min",
    "color_panel_extra_sat_max", "color_panel_value_min", "color_panel_gray_min",
    "color_panel_spread_min", "color_panel_close_x", "color_panel_close_y",
    "color_panel_min_area", "color_panel_min_width_px", "color_panel_min_width_ratio",
    "color_panel_min_height_px", "color_panel_max_height_ratio", "color_panel_min_coverage",
    "color_panel_min_aspect", "color_panel_expand_x", "color_panel_expand_y",
    "color_panel_min_sample_pixels", "color_panel_min_color_spread",
    "color_panel_rebuild_gray_min", "color_panel_alpha", "color_panel_edge_canny_low",
    "color_panel_edge_canny_high", "color_panel_edge_dark_gray_max",
    "color_panel_edge_color_sat_min", "color_panel_edge_color_gray_max",
    "color_panel_protect_from_corridor", "color_panel_rebuild_fallback",
    "color_panel_patch_alpha", "color_panel_local_patch", "color_panel_local_patch_dilate_x", "color_panel_local_patch_dilate_y",
    "color_panel_overflow_trim", "color_panel_overflow_safe_dilate_x", "color_panel_overflow_safe_dilate_y",
    "color_panel_overflow_near_dilate_x", "color_panel_overflow_near_dilate_y", "color_panel_overflow_white",
    "color_panel_outside_strip_clean", "color_panel_outside_strip_near_dilate_x", "color_panel_outside_strip_near_dilate_y",
    "color_panel_outside_strip_safe_dilate_x", "color_panel_outside_strip_safe_dilate_y",
    "color_panel_outside_strip_gray_min", "color_panel_outside_strip_sat_max", "color_panel_outside_strip_spread_max",
    "color_panel_outside_strip_close_x", "color_panel_outside_strip_close_y",
    "color_panel_hole_gray_min", "color_panel_hole_sat_max",
    "color_panel_hole_spread_max", "color_panel_hole_ratio_trigger",
    "color_panel_edge_hole_ratio_trigger", "color_panel_edge_check_px",
    "colored_panel_watermark_cleanup", "colored_panel_wm_min_band_pixels",
    "colored_panel_wm_min_flat_sample", "colored_panel_wm_flat_std_max",
    "colored_panel_wm_flat_delta_p95_max", "colored_panel_wm_real_text_gray_max",
    "colored_panel_wm_real_edge_gray_max", "colored_panel_wm_edge_canny_low",
    "colored_panel_wm_edge_canny_high", "colored_panel_wm_content_guard_dilate_x",
    "colored_panel_wm_content_guard_dilate_y", "colored_panel_wm_original_gray_min",
    "colored_panel_wm_gray_min", "colored_panel_wm_gray_max", "colored_panel_wm_sat_max",
    "colored_panel_wm_spread_max", "colored_panel_wm_min_delta",
    "colored_panel_wm_min_darkening", "colored_panel_wm_min_pixels",
    "colored_panel_wm_close_x", "colored_panel_wm_close_y",
    "colored_panel_wm_dilate_x", "colored_panel_wm_dilate_y", "colored_panel_wm_alpha",
    "graph_restore_enabled", "graph_roi_dilate_x", "graph_roi_dilate_y",
    "graph_restore_min_roi_pixels", "graph_restore_min_pixels", "graph_restore_min_evidence_pixels",
    "graph_restore_min_confidence", "graph_restore_dilate", "graph_table_exclude_dilate_x",
    "graph_table_exclude_dilate_y", "graph_table_border_min_len_x", "graph_table_border_min_len_y",
    "graph_black_gray_max", "graph_black_aa_gray_max", "graph_black_weaken_diff", "graph_edge_canny_low", "graph_edge_canny_high",
    "graph_line_min_len", "graph_dashed_close_px", "graph_blue_hue_min", "graph_blue_hue_max",
    "graph_blue_sat_min", "graph_blue_sat_min_alt", "graph_blue_sat_min_clean", "graph_blue_gray_max",
    "graph_blue_b_minus_r_min", "graph_blue_b_minus_g_min", "graph_blue_min_component_area",
    "graph_blue_min_component_span", "graph_blue_weaken_diff", "graph_blue_sat_loss", "graph_restore_markers", "graph_marker_gray_max",
    "graph_marker_min_area", "graph_marker_max_area",
    "table_aware_cleanup", "table_cell_gray_min", "table_cell_value_min",
    "table_cell_sat_min", "table_cell_sat_max", "table_cell_spread_min",
    "table_cell_min_area", "table_cell_min_width_px", "table_cell_min_height_px",
    "table_cell_max_width_ratio", "table_cell_max_height_ratio", "table_cell_min_coverage",
    "table_cell_min_sample_pixels", "table_cell_min_band_pixels", "table_cell_min_color_spread",
    "table_band_intersect_dilate_x", "table_band_intersect_dilate_y",
    "table_near_dilate_x", "table_near_dilate_y", "table_bg_patch_alpha",
    "table_bg_patch_gray_min", "table_bg_patch_sat_max", "table_bg_patch_spread_max",
    "table_bg_patch_diag_dilate_x", "table_bg_patch_diag_dilate_y",
    "table_stroke_gray_max", "table_stroke_sat_min", "table_stroke_color_gray_max",
    "table_border_min_len_x", "table_border_min_len_y", "table_circle_canny_low",
    "table_circle_canny_high", "table_compact_stroke_min_area",
    "table_compact_stroke_max_area", "table_compact_stroke_max_w",
    "table_compact_stroke_max_h", "table_restore_stroke_dilate",
}


def create_settings_from_config(config: Mapping[str, Any] | None = None) -> ProcessingSettings:
    """Build a V6.9.5-compatible ProcessingSettings object from config.json."""
    cfg = dict(config or {})
    known = {
        "top_template_path", "bottom_template_path", "diag_template_path",
        "top_threshold", "bottom_threshold", "diag_threshold", "mode",
        "protect_gray_threshold", "min_matched_regions", "header_height",
        "footer_height", "output_grayscale", "graph_safe_paper_mode",
    }
    kwargs = {k: cfg[k] for k in known if k in cfg}
    kwargs["extra"] = {k: v for k, v in cfg.items() if k in _V67_ARG_KEYS}
    return ProcessingSettings(**kwargs)


def _namespace_from_settings(settings: ProcessingSettings | Mapping[str, Any] | None = None) -> argparse.Namespace:
    # Reuse the CLI parser so defaults never drift between standalone and GUI use.
    args = parse_args(["__dummy__.pdf"])
    args.save_pages = None
    args.debug = None
    args.worker_args_pkl = None
    args.page_index = None
    args.page_output = None
    args._need_debug = False

    if settings is None:
        settings = ProcessingSettings()
    elif isinstance(settings, Mapping):
        settings = create_settings_from_config(settings)

    # Map a couple of legacy settings into V6.9 behavior.
    args.safe_text_gray_max = min(int(getattr(settings, "protect_gray_threshold", 105)), 105)
    args.line_restore = bool(getattr(settings, "graph_safe_paper_mode", True))

    for key, value in getattr(settings, "extra", {}).items():
        if hasattr(args, key):
            setattr(args, key, value)

    # V7.0 is intended to preserve original colors. The GUI can still export
    # grayscale later if the user explicitly enables output_grayscale.
    return apply_v7_mode_presets(args)


def process_page_rgb(page_rgb: np.ndarray, settings: ProcessingSettings | Mapping[str, Any] | None = None):
    """
    Process one RGB page and return `(cleaned_rgb, debug_masks, meta)`.

    This is the API expected by processors.py in the unified GUI.
    """
    if page_rgb is None or not isinstance(page_rgb, np.ndarray):
        raise TypeError("page_rgb must be a numpy.ndarray")
    if page_rgb.ndim == 2:
        page_rgb = cv2.cvtColor(page_rgb, cv2.COLOR_GRAY2RGB)
    if page_rgb.ndim != 3 or page_rgb.shape[2] != 3:
        raise ValueError("page_rgb must be RGB with 3 channels")
    if page_rgb.dtype != np.uint8:
        page_rgb = np.clip(page_rgb, 0, 255).astype(np.uint8)

    args = _namespace_from_settings(settings)
    bgr = cv2.cvtColor(page_rgb, cv2.COLOR_RGB2BGR)
    cleaned_bgr, debug = clean_page_bgr(bgr, args)
    cleaned_rgb = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)
    meta = {
        "engine": "TDM V7.7.6 Colored-panel Watermark Cleanup",
        "safe_mode": True,
        "line_restore": bool(args.line_restore),
        "header_right_protect": bool(args.header_right_protect),
    }
    return cleaned_rgb, debug, meta


def main() -> None:
    args = parse_args()
    if args.worker_args_pkl is not None:
        with open(args.worker_args_pkl, "rb") as f:
            worker_args = pickle.load(f)
        if args.page_index is None or args.page_output is None:
            raise SystemExit("Worker mode requires --page-index and --page-output")
        process_single_page_to_jpg(args.input_pdf, int(args.page_index), args.page_output, worker_args)
        return
    process_pdf(args.input_pdf, args.output, args)


if __name__ == "__main__":
    main()
