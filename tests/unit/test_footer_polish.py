from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.engine.raster.footer_polish import (
    FooterPolishConfig,
    FooterPolishMetrics,
    score_footer_residual,
    polish_footer_residual,
)


def _make_synthetic_page(
    *,
    has_watermark: bool = True,
    watermark_intensity: int = 140,
    has_rule: bool = True,
    has_page_number: bool = True,
    has_upper_content: bool = True,
    has_colored_badge: bool = False,
    page_shape: tuple[int, int] = (1000, 800),
) -> np.ndarray:
    """Construct an off-white RGB page with synthetic elements."""
    height, width = page_shape
    # Off-white / cream paper background
    page = np.full((height, width, 3), (252, 250, 246), dtype=np.uint8)

    # Upper content above y = 0.945 * H (e.g. blue header / chapter text)
    if has_upper_content:
        cv2.putText(
            page,
            "CHUONG 3: GIAI TICH VA HINH HOC TOAN CAO CAP",
            (80, int(round(0.925 * height))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (30, 80, 210),
            2,
            cv2.LINE_AA,
        )

    # Thin legitimate footer rule at y = 0.956 * H (intersects footer strip)
    rule_y = int(round(0.956 * height))
    if has_rule:
        cv2.line(
            page,
            (int(round(0.08 * width)), rule_y),
            (int(round(0.92 * width)), rule_y),
            (70, 70, 70),
            1,
        )

    # Page-number glyphs on lower-right inside page_number_guard (x >= 0.80 * W)
    if has_page_number:
        cv2.putText(
            page,
            "- Trang 42 -",
            (int(round(0.82 * width)), int(round(0.982 * height))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (35, 35, 35),
            2,
            cv2.LINE_AA,
        )

    # Optional colored badge inside footer ROI (should be preserved by color guard)
    if has_colored_badge:
        cv2.circle(
            page,
            (int(round(0.34 * width)), int(round(0.963 * height))),
            5,
            (20, 20, 220),
            -1,
        )

    # Centered pale-gray footer URL glyphs inside footer_url_box (x0=0.32, y0=0.955, x1=0.75, y1=0.997)
    if has_watermark:
        cv2.putText(
            page,
            "https://TaiLieuOnThi.Net",
            (int(round(0.33 * width)), int(round(0.985 * height))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.78,
            (watermark_intensity, watermark_intensity, watermark_intensity),
            2,
            cv2.LINE_AA,
        )

    return page


def test_dirty_footer_scores_high():
    dirty_page = _make_synthetic_page(has_watermark=True)
    config = FooterPolishConfig()
    score = score_footer_residual(dirty_page, config=config)
    assert score > 0.035, f"Expected dirty score > 0.035, got {score:.4f}"


def test_clean_fixture_produces_zero_changed_pixels_and_exact_equality():
    clean_page = _make_synthetic_page(has_watermark=False)
    config = FooterPolishConfig()
    polished, metrics = polish_footer_residual(clean_page, config=config)

    assert metrics.changed_pixels == 0
    assert metrics.residual_score_before <= 0.035
    assert metrics.residual_score_after <= 0.035
    assert metrics.protected_change_ratio == 0.0
    np.testing.assert_array_equal(polished, clean_page)


def test_polish_reduces_score_below_threshold():
    dirty_page = _make_synthetic_page(has_watermark=True)
    config = FooterPolishConfig(level="auto")
    polished, metrics = polish_footer_residual(dirty_page, config=config)

    assert metrics.residual_score_before > 0.035
    assert metrics.residual_score_after <= 0.035
    assert metrics.changed_pixels > 0
    assert metrics.protected_change_ratio <= 0.002
    assert metrics.level_used in ("standard", "deep")

    # Score after polish should match metrics.residual_score_after
    rescored = score_footer_residual(polished, config=config)
    assert rescored <= 0.035
    assert abs(rescored - metrics.residual_score_after) < 1e-4


def test_page_numbers_and_upper_content_strictly_unchanged():
    dirty_page = _make_synthetic_page(has_watermark=True)
    config = FooterPolishConfig()
    polished, metrics = polish_footer_residual(dirty_page, config=config)

    height, width = dirty_page.shape[:2]

    # 1. Page number guard region (x >= 0.80 * W, y >= 0.955 * H)
    px0 = int(round(config.page_number_guard[0] * width))
    py0 = int(round(config.page_number_guard[1] * height))
    np.testing.assert_array_equal(
        polished[py0:, px0:],
        dirty_page[py0:, px0:],
        err_msg="Page number guard region was altered!",
    )

    # 2. Upper content guard (y < 0.945 * H)
    uy = int(round(config.upper_content_guard_y * height))
    np.testing.assert_array_equal(
        polished[:uy, :],
        dirty_page[:uy, :],
        err_msg="Upper content above upper_content_guard_y was altered!",
    )

    # 3. Legitimate thin footer rule must remain unchanged
    rule_y = int(round(0.956 * height))
    np.testing.assert_array_equal(
        polished[rule_y, :],
        dirty_page[rule_y, :],
        err_msg="Legitimate horizontal rule was whitened or altered!",
    )

    # 4. Protected change ratio is 0.0
    assert metrics.protected_change_ratio == 0.0


def test_standard_and_deep_levels():
    dirty_page = _make_synthetic_page(has_watermark=True)

    # Standard
    std_config = FooterPolishConfig(level="standard")
    std_out, std_metrics = polish_footer_residual(dirty_page, config=std_config)
    assert std_metrics.level_used == "standard"
    assert std_metrics.changed_pixels > 0
    assert std_metrics.residual_score_after <= 0.035

    # Deep
    deep_config = FooterPolishConfig(level="deep")
    deep_out, deep_metrics = polish_footer_residual(dirty_page, config=deep_config)
    assert deep_metrics.level_used == "deep"
    assert deep_metrics.changed_pixels > 0
    assert deep_metrics.residual_score_after <= 0.035
    assert deep_metrics.protected_change_ratio <= 0.002


def test_auto_escalation_when_standard_residual_high(monkeypatch):
    """When standard cleanup cannot achieve score <= 0.035, auto escalates to deep."""
    dirty_page = _make_synthetic_page(has_watermark=True)
    config = FooterPolishConfig(level="auto")

    # If standard score remains above threshold, verify auto escalates to deep
    import backend.engine.raster.footer_polish as fp_mod

    orig_apply = fp_mod._apply_polish

    def mock_apply_polish(rgb, *, level, config, external_guards):
        if level == "standard":
            # Return standard without cleaning fully
            return rgb.copy(), external_guards.copy()
        return orig_apply(rgb, level=level, config=config, external_guards=external_guards)

    monkeypatch.setattr(fp_mod, "_apply_polish", mock_apply_polish)

    polished, metrics = fp_mod.polish_footer_residual(dirty_page, config=config)
    assert metrics.level_used == "deep"
    assert metrics.residual_score_after <= 0.035


def test_content_protect_mask_honored():
    dirty_page = _make_synthetic_page(has_watermark=True)
    height, width = dirty_page.shape[:2]

    # Create a protect mask covering a sub-block in the footer URL box
    protect_mask = np.zeros((height, width), dtype=np.uint8)
    bx0, by0 = int(round(0.40 * width)), int(round(0.965 * height))
    bx1, by1 = int(round(0.45 * width)), int(round(0.985 * height))
    protect_mask[by0:by1, bx0:bx1] = 255

    config = FooterPolishConfig(level="deep")
    polished, metrics = polish_footer_residual(
        dirty_page,
        config=config,
        content_protect_mask=protect_mask,
    )

    # The masked region must be byte-for-byte identical
    np.testing.assert_array_equal(
        polished[by0:by1, bx0:bx1],
        dirty_page[by0:by1, bx0:bx1],
    )
    assert metrics.protected_change_ratio == 0.0


def test_colored_content_in_roi_preserved():
    dirty_page = _make_synthetic_page(
        has_watermark=True,
        has_colored_badge=True,
    )
    height, width = dirty_page.shape[:2]
    badge_x, badge_y = int(round(0.34 * width)), int(round(0.963 * height))

    config = FooterPolishConfig(level="deep")
    polished, metrics = polish_footer_residual(dirty_page, config=config)

    # Red badge must be preserved
    np.testing.assert_array_equal(
        polished[badge_y - 2 : badge_y + 3, badge_x - 2 : badge_x + 3],
        dirty_page[badge_y - 2 : badge_y + 3, badge_x - 2 : badge_x + 3],
    )
    assert metrics.protected_change_ratio <= 0.002


def test_invalid_inputs_and_edge_cases():
    # 1. Invalid input shape / ndim
    with pytest.raises(ValueError, match="must be an RGB image"):
        score_footer_residual(np.zeros((100, 100), dtype=np.uint8), config=FooterPolishConfig())

    with pytest.raises(ValueError, match="must be an RGB image"):
        polish_footer_residual(np.zeros((100, 100, 4), dtype=np.uint8), config=FooterPolishConfig())

    # 2. Content protect mask shape mismatch
    with pytest.raises(ValueError, match="shape.*does not match"):
        score_footer_residual(
            np.zeros((100, 100, 3), dtype=np.uint8),
            config=FooterPolishConfig(),
            content_protect_mask=np.zeros((50, 50), dtype=np.uint8),
        )

    # 3. Unknown cleanup level
    with pytest.raises(ValueError, match="Unknown footer cleanup level"):
        polish_footer_residual(
            np.zeros((100, 100, 3), dtype=np.uint8),
            config=FooterPolishConfig(level="invalid"),  # type: ignore
        )

    # 4. Inverted / empty ROI
    empty_cfg = FooterPolishConfig(footer_url_box=(0.5, 0.5, 0.4, 0.4))
    score = score_footer_residual(np.zeros((100, 100, 3), dtype=np.uint8), config=empty_cfg)
    assert score == 0.0

    out, m = polish_footer_residual(np.zeros((100, 100, 3), dtype=np.uint8), config=empty_cfg)
    assert m.changed_pixels == 0
    assert m.residual_score_before == 0.0
    assert m.residual_score_after == 0.0


def test_tiny_narrow_rois_do_not_crash():
    """Verify tiny/narrow ROIs (e.g. roi_w = 20, roi_h = 4) do not crash score or polish."""
    img = np.full((100, 100, 3), (250, 250, 250), dtype=np.uint8)
    # Add dark residual pixels inside lower strip to exercise detection and polish
    img[96:100, 10:30] = (150, 150, 150)

    # 1. Narrow ROI (w=20, h=4) with standard and deep cleanup
    for level in ("standard", "deep", "auto"):
        cfg_narrow = FooterPolishConfig(
            footer_url_box=(0.10, 0.96, 0.30, 1.00),
            upper_content_guard_y=0.90,
            level=level,  # type: ignore
        )
        score = score_footer_residual(img, config=cfg_narrow)
        assert isinstance(score, float)
        assert score >= 0.0

        polished, metrics = polish_footer_residual(img, config=cfg_narrow)
        assert polished.shape == img.shape
        assert isinstance(metrics, FooterPolishMetrics)

    # 2. Sub-3px ROI (w=2, h=2) exercising blur bypass
    img_sub3 = np.full((100, 100, 3), (250, 250, 250), dtype=np.uint8)
    img_sub3[98:100, 10:12] = (150, 150, 150)
    cfg_sub3 = FooterPolishConfig(
        footer_url_box=(0.10, 0.98, 0.12, 1.00),
        upper_content_guard_y=0.90,
        level="deep",
    )
    score_sub3 = score_footer_residual(img_sub3, config=cfg_sub3)
    assert isinstance(score_sub3, float)
    polished_sub3, metrics_sub3 = polish_footer_residual(img_sub3, config=cfg_sub3)
    assert polished_sub3.shape == img_sub3.shape
    assert isinstance(metrics_sub3, FooterPolishMetrics)


def test_ratio_box_to_pixels_public_and_compatibility():
    """Verify ratio_box_to_pixels is exposed publicly and backward compatible with alias."""
    from backend.engine.raster.footer_polish import ratio_box_to_pixels, _ratio_box_to_pixels

    box = (0.1, 0.2, 0.5, 0.6)
    px = ratio_box_to_pixels(box, 1000, 800)
    assert px == _ratio_box_to_pixels(box, 1000, 800)
    assert px == (100, 160, 500, 480)


