from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

FooterCleanupLevel = Literal["auto", "standard", "deep"]


@dataclass(frozen=True, slots=True)
class FooterPolishConfig:
    level: FooterCleanupLevel = "auto"
    footer_url_box: tuple[float, float, float, float] = (0.32, 0.955, 0.75, 0.997)
    page_number_guard: tuple[float, float, float, float] = (0.80, 0.955, 1.00, 1.00)
    upper_content_guard_y: float = 0.945
    residual_threshold: float = 0.035


@dataclass(frozen=True, slots=True)
class FooterPolishMetrics:
    level_used: str
    changed_pixels: int
    residual_score_before: float
    residual_score_after: float
    protected_change_ratio: float


def _ratio_box_to_pixels(
    box: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Convert normalized (x0, y0, x1, y1) box to pixel coordinates."""
    x0 = max(0, min(width, int(round(box[0] * width))))
    y0 = max(0, min(height, int(round(box[1] * height))))
    x1 = max(0, min(width, int(round(box[2] * width))))
    y1 = max(0, min(height, int(round(box[3] * height))))
    if x1 <= x0 or y1 <= y0:
        return 0, 0, 0, 0
    return x0, y0, x1, y1


def _build_external_guards(
    shape: tuple[int, int],
    config: FooterPolishConfig,
    content_protect_mask: np.ndarray | None,
) -> np.ndarray:
    """Build boolean mask of protected pixels across full native image."""
    height, width = shape
    guard = np.zeros((height, width), dtype=bool)

    # 1. Upper content guard (y < upper_content_guard_y * H)
    uy = max(0, min(height, int(round(config.upper_content_guard_y * height))))
    guard[:uy, :] = True

    # 2. Page number guard
    px0, py0, px1, py1 = _ratio_box_to_pixels(config.page_number_guard, width, height)
    if px1 > px0 and py1 > py0:
        guard[py0:py1, px0:px1] = True

    # 3. Content protect mask
    if content_protect_mask is not None:
        cp = np.asarray(content_protect_mask)
        if cp.shape != (height, width):
            raise ValueError(
                f"content_protect_mask shape {cp.shape} does not match image shape {(height, width)}"
            )
        guard |= (cp > 0)

    return guard


def _detect_roi_elements(
    roi_rgb: np.ndarray,
    roi_external_guard: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Analyze ROI to detect rule guard, color guard, local background, and paper level.

    Returns:
        (roi_guard, local_bg_rgb, local_bg_gray, paper_level)
    """
    roi_h, roi_w = roi_rgb.shape[:2]
    roi_gray = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2GRAY)
    sat_roi = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2HSV)[..., 1]

    unprotected_ext = ~roi_external_guard
    if not np.any(unprotected_ext):
        paper_level = 255.0
        roi_guard = np.ones((roi_h, roi_w), dtype=bool)
        local_bg_rgb = roi_rgb.astype(np.float32)
        local_bg_gray = roi_gray.astype(np.float32)
        return roi_guard, local_bg_rgb, local_bg_gray, paper_level

    # Estimate paper background level from clean unprotected pixels
    paper_level = float(np.percentile(roi_gray[unprotected_ext], 95))

    # Legitimate thin horizontal rule detection:
    # A legitimate rule is a continuous horizontal stroke across a significant span
    dark_pixels = (roi_gray < paper_level - 25) & (sat_roi <= 35) & unprotected_ext
    rule_klen = max(35, int(round(roi_w * 0.10)))
    rule_open = cv2.morphologyEx(
        dark_pixels.astype(np.uint8),
        cv2.MORPH_OPEN,
        np.ones((1, rule_klen), dtype=np.uint8),
    )
    rule_guard = cv2.dilate(rule_open, np.ones((3, 3), dtype=np.uint8), iterations=1) > 0

    # Colored content guard (saturation check + 1px edge dilation)
    raw_color = (sat_roi > 30) & unprotected_ext
    color_guard = (
        cv2.dilate(raw_color.astype(np.uint8), np.ones((3, 3), dtype=np.uint8), iterations=1) > 0
    )

    # Combined ROI guard
    roi_guard = roi_external_guard | rule_guard | color_guard
    unprotected = ~roi_guard

    if not np.any(unprotected):
        local_bg_rgb = roi_rgb.astype(np.float32)
        local_bg_gray = roi_gray.astype(np.float32)
        return roi_guard, local_bg_rgb, local_bg_gray, paper_level

    # Robust local paper background reconstruction
    paper_rgb = np.array([
        np.percentile(roi_rgb[..., c][unprotected], 95)
        for c in range(3)
    ], dtype=np.float32)

    bg_work = roi_rgb.astype(np.float32).copy()
    non_paper = roi_guard | (roi_gray < paper_level - 12)
    bg_work[non_paper] = paper_rgb

    # Smooth local paper background estimate
    kx = min(roi_w - (1 - roi_w % 2), 51)
    ky = min(roi_h - (1 - roi_h % 2), 25)
    kx = max(5, kx)
    ky = max(5, ky)
    local_bg_rgb = cv2.GaussianBlur(bg_work, (kx, ky), 0)
    local_bg_gray = cv2.cvtColor(
        np.clip(local_bg_rgb, 0, 255).astype(np.uint8),
        cv2.COLOR_RGB2GRAY,
    ).astype(np.float32)

    return roi_guard, local_bg_rgb, local_bg_gray, paper_level


def _extract_candidate_residue(
    roi_gray: np.ndarray,
    sat_roi: np.ndarray,
    local_bg_gray: np.ndarray,
    roi_guard: np.ndarray,
    *,
    min_contrast: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Find candidate residual components rejecting protected pixels and broad texture.

    Returns:
        (cand_mask, pos_contrast)
    """
    roi_h, roi_w = roi_gray.shape[:2]
    unprotected = ~roi_guard

    local_contrast = local_bg_gray - roi_gray.astype(np.float32)
    pos_contrast = np.maximum(0.0, local_contrast)

    # Low-saturation positive local contrast above noise floor
    raw_cand = unprotected & (sat_roi <= 35) & (pos_contrast >= min_contrast)

    # Connected component analysis to reject broad texture and isolated noise
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        raw_cand.astype(np.uint8), connectivity=8
    )
    cand_mask = np.zeros_like(raw_cand, dtype=bool)
    max_comp_area = int(round(0.50 * roi_w * roi_h))

    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        comp_w = stats[i, cv2.CC_STAT_WIDTH]
        comp_h = stats[i, cv2.CC_STAT_HEIGHT]
        # Discard single-pixel noise and broad background texture
        if area >= 2 and not (
            area > max_comp_area
            or (comp_w > int(0.85 * roi_w) and comp_h > int(0.75 * roi_h))
        ):
            cand_mask[labels == i] = True

    return cand_mask, pos_contrast


def score_footer_residual(
    rgb: np.ndarray,
    *,
    config: FooterPolishConfig,
    content_protect_mask: np.ndarray | None = None,
) -> float:
    """Score remaining watermark contrast in footer ROI normalized by (255 * ROI pixel count)."""
    arr = np.asarray(rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError("rgb must be an RGB image with shape (H, W, 3)")

    height, width = arr.shape[:2]
    rx0, ry0, rx1, ry1 = _ratio_box_to_pixels(config.footer_url_box, width, height)
    roi_pixel_count = (rx1 - rx0) * (ry1 - ry0)
    if roi_pixel_count <= 0:
        return 0.0

    external_guards = _build_external_guards(
        (height, width), config, content_protect_mask
    )
    roi_rgb = arr[ry0:ry1, rx0:rx1]
    roi_gray = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2GRAY)
    sat_roi = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2HSV)[..., 1]
    roi_ext_guard = external_guards[ry0:ry1, rx0:rx1]

    roi_guard, _, local_bg_gray, _ = _detect_roi_elements(roi_rgb, roi_ext_guard)
    cand_mask, pos_contrast = _extract_candidate_residue(
        roi_gray, sat_roi, local_bg_gray, roi_guard, min_contrast=3.0
    )

    if not np.any(cand_mask):
        return 0.0

    residual_contrast = np.where(cand_mask, pos_contrast, 0.0)
    summed = float(np.sum(residual_contrast))
    score = float(summed / (255.0 * roi_pixel_count))
    return float(np.clip(score, 0.0, 1.0))


def _apply_polish(
    native_rgb: np.ndarray,
    *,
    level: Literal["standard", "deep"],
    config: FooterPolishConfig,
    external_guards: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Run standard or deep polish on native resolution image.

    Returns:
        (polished_rgb, full_page_guard_mask)
    """
    height, width = native_rgb.shape[:2]
    rx0, ry0, rx1, ry1 = _ratio_box_to_pixels(config.footer_url_box, width, height)
    if rx1 <= rx0 or ry1 <= ry0:
        return native_rgb.copy(), external_guards.copy()

    roi_rgb = native_rgb[ry0:ry1, rx0:rx1]
    roi_gray = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2GRAY)
    sat_roi = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2HSV)[..., 1]
    roi_ext_guard = external_guards[ry0:ry1, rx0:rx1]

    roi_guard, local_bg_rgb, local_bg_gray, _ = _detect_roi_elements(
        roi_rgb, roi_ext_guard
    )

    # Full page guard incorporating ROI rule and color guards
    full_guard = external_guards.copy()
    full_guard[ry0:ry1, rx0:rx1] = roi_guard

    if level == "standard":
        min_contrast = 4.0
        dilation_iters = 1
    elif level == "deep":
        min_contrast = 2.0
        dilation_iters = 2
    else:
        raise ValueError(f"Unknown cleanup level: {level}")

    cand_mask, _ = _extract_candidate_residue(
        roi_gray, sat_roi, local_bg_gray, roi_guard, min_contrast=min_contrast
    )

    if not np.any(cand_mask):
        return native_rgb.copy(), full_guard

    # Dilate candidate residue conservatively
    dilated_cand = cv2.dilate(
        cand_mask.astype(np.uint8),
        np.ones((3, 3), dtype=np.uint8),
        iterations=dilation_iters,
    ) > 0

    # Ensure guards are strictly never modified
    modify_mask = dilated_cand & ~roi_guard

    out = native_rgb.copy()
    out_roi = roi_rgb.copy()
    out_roi[modify_mask] = np.clip(local_bg_rgb[modify_mask], 0, 255).astype(np.uint8)
    out[ry0:ry1, rx0:rx1] = out_roi
    return out, full_guard


def _compute_protected_change_ratio(
    native_rgb: np.ndarray,
    out_rgb: np.ndarray,
    full_guard: np.ndarray,
    config: FooterPolishConfig,
) -> float:
    """Compute (changed protected pixels) / (total protected pixels in ROI or 1)."""
    height, width = native_rgb.shape[:2]
    rx0, ry0, rx1, ry1 = _ratio_box_to_pixels(config.footer_url_box, width, height)

    roi_mask = np.zeros((height, width), dtype=bool)
    if rx1 > rx0 and ry1 > ry0:
        roi_mask[ry0:ry1, rx0:rx1] = True

    diff = np.any(out_rgb != native_rgb, axis=-1)

    # Any pixel outside ROI or inside guards is protected
    is_protected = (~roi_mask) | full_guard
    total_protected_in_roi = int(np.count_nonzero(full_guard & roi_mask))
    changed_protected = int(np.count_nonzero(diff & is_protected))

    denom = max(1, total_protected_in_roi)
    return float(changed_protected / denom)


def polish_footer_residual(
    native_rgb: np.ndarray,
    *,
    config: FooterPolishConfig,
    content_protect_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, FooterPolishMetrics]:
    """Polish footer residual at native resolution preserving guards and legitimate lines."""
    arr = np.asarray(native_rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError("native_rgb must be an RGB image with shape (H, W, 3)")

    height, width = arr.shape[:2]
    external_guards = _build_external_guards(
        (height, width), config, content_protect_mask
    )

    score_before = score_footer_residual(
        arr, config=config, content_protect_mask=content_protect_mask
    )

    if config.level == "auto":
        # If clean initially, do not modify the image
        if score_before <= config.residual_threshold:
            return arr.copy(), FooterPolishMetrics(
                level_used="standard",
                changed_pixels=0,
                residual_score_before=score_before,
                residual_score_after=score_before,
                protected_change_ratio=0.0,
            )

        # 1. Run Standard first
        std_out, std_guard = _apply_polish(
            arr,
            level="standard",
            config=config,
            external_guards=external_guards,
        )
        score_std = score_footer_residual(
            std_out,
            config=config,
            content_protect_mask=content_protect_mask,
        )

        # 2. If standard passed threshold, stop and return standard
        if score_std <= config.residual_threshold:
            changed = int(np.count_nonzero(np.any(std_out != arr, axis=-1)))
            ratio = _compute_protected_change_ratio(
                arr, std_out, std_guard, config
            )
            return std_out, FooterPolishMetrics(
                level_used="standard",
                changed_pixels=changed,
                residual_score_before=score_before,
                residual_score_after=score_std,
                protected_change_ratio=ratio,
            )

        # 3. Otherwise escalate to Deep
        deep_out, deep_guard = _apply_polish(
            arr,
            level="deep",
            config=config,
            external_guards=external_guards,
        )
        score_deep = score_footer_residual(
            deep_out,
            config=config,
            content_protect_mask=content_protect_mask,
        )
        changed = int(np.count_nonzero(np.any(deep_out != arr, axis=-1)))
        ratio = _compute_protected_change_ratio(
            arr, deep_out, deep_guard, config
        )
        return deep_out, FooterPolishMetrics(
            level_used="deep",
            changed_pixels=changed,
            residual_score_before=score_before,
            residual_score_after=score_deep,
            protected_change_ratio=ratio,
        )

    elif config.level == "standard":
        std_out, std_guard = _apply_polish(
            arr,
            level="standard",
            config=config,
            external_guards=external_guards,
        )
        score_std = score_footer_residual(
            std_out,
            config=config,
            content_protect_mask=content_protect_mask,
        )
        changed = int(np.count_nonzero(np.any(std_out != arr, axis=-1)))
        ratio = _compute_protected_change_ratio(arr, std_out, std_guard, config)
        return std_out, FooterPolishMetrics(
            level_used="standard",
            changed_pixels=changed,
            residual_score_before=score_before,
            residual_score_after=score_std,
            protected_change_ratio=ratio,
        )

    elif config.level == "deep":
        deep_out, deep_guard = _apply_polish(
            arr,
            level="deep",
            config=config,
            external_guards=external_guards,
        )
        score_deep = score_footer_residual(
            deep_out,
            config=config,
            content_protect_mask=content_protect_mask,
        )
        changed = int(np.count_nonzero(np.any(deep_out != arr, axis=-1)))
        ratio = _compute_protected_change_ratio(arr, deep_out, deep_guard, config)
        return deep_out, FooterPolishMetrics(
            level_used="deep",
            changed_pixels=changed,
            residual_score_before=score_before,
            residual_score_after=score_deep,
            protected_change_ratio=ratio,
        )

    else:
        raise ValueError(f"Unknown footer cleanup level: {config.level}")
