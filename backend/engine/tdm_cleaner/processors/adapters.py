from __future__ import annotations

from typing import Any, Mapping
import copy

import cv2
import numpy as np

from .base import BaseProcessor
from .config import ProcessorConfig
from .engine_loader import load_engine
from .image_utils import ensure_rgb_uint8, extract_image


class EbookProcessor(BaseProcessor):
    """Adapter for the legacy EBOOK engine; keeps the original image algorithm intact."""

    mode = "ebook"

    def _engine_and_settings(self):
        cached = getattr(self, "_cached_engine_settings", None)
        if cached is not None:
            return cached

        engine = load_engine(self.config.engine_path("ebook"), "ebook")
        if hasattr(engine, "create_settings_from_config"):
            settings = engine.create_settings_from_config(self.config.params)
        else:
            settings = engine.ProcessingSettings(
                top_template_path=self.config.get("top_template_path", "builtin"),
                bottom_template_path=self.config.get("bottom_template_path", "builtin"),
                diag_template_path=self.config.get("diag_template_path", "builtin"),
                top_threshold=float(self.config.get("top_threshold", 0.55)),
                bottom_threshold=float(self.config.get("bottom_threshold", 0.55)),
                diag_threshold=float(self.config.get("diag_threshold", 0.25)),
                protect_gray_threshold=int(self.config.get("protect_gray_threshold", 105)),
                min_matched_regions=int(self.config.get("min_matched_regions", 1)),
                header_height=str(self.config.get("header_height", "0%")),
                footer_height=str(self.config.get("footer_height", "0%")),
                output_grayscale=bool(self.config.get("output_grayscale", False)),
                graph_safe_paper_mode=bool(self.config.get("graph_safe_paper_mode", True)),
                ebook_skip_color_cover_page=bool(self.config.get("ebook_skip_color_cover_page", True)),
                ebook_ghost_cleanup=bool(self.config.get("ebook_ghost_cleanup", True)),
                ebook_ghost_strength=float(self.config.get("ebook_ghost_strength", 0.66)),
                ebook_ghost_gray_min=int(self.config.get("ebook_ghost_gray_min", 138)),
                ebook_ghost_gray_max=int(self.config.get("ebook_ghost_gray_max", 246)),
                ebook_ghost_sat_max=int(self.config.get("ebook_ghost_sat_max", 56)),
                ebook_ghost_band_ratio=float(self.config.get("ebook_ghost_band_ratio", 0.128)),
                ebook_ghost_content_dark_gray=int(self.config.get("ebook_ghost_content_dark_gray", 158)),
                ebook_ghost_colored_sat_min=int(self.config.get("ebook_ghost_colored_sat_min", 38)),
                ebook_ghost_edge_dilate=int(self.config.get("ebook_ghost_edge_dilate", 3)),
                ebook_ghost_mask_dilate=int(self.config.get("ebook_ghost_mask_dilate", 2)),
                ebook_ghost_bg_median_kernel=int(self.config.get("ebook_ghost_bg_median_kernel", 35)),
                ebook_ghost_bg_gauss_sigma=float(self.config.get("ebook_ghost_bg_gauss_sigma", 7.0)),
                ebook_ghost_max_darken_to_bg=int(self.config.get("ebook_ghost_max_darken_to_bg", 55)),
                ebook_ghost_require_local_darker=bool(self.config.get("ebook_ghost_require_local_darker", True)),
                ebook_ghost_local_diff_min=int(self.config.get("ebook_ghost_local_diff_min", 2)),
                ebook_ghost_local_diff_max=int(self.config.get("ebook_ghost_local_diff_max", 70)),
                ebook_cover_color_area_min=float(self.config.get("ebook_cover_color_area_min", 0.22)),
                ebook_cover_paper_area_max=float(self.config.get("ebook_cover_paper_area_max", 0.78)),
                ebook_preserve_images=bool(self.config.get("ebook_preserve_images", True)),
                ebook_image_sat_min=int(self.config.get("ebook_image_sat_min", 28)),
                ebook_image_min_box_area_ratio=float(self.config.get("ebook_image_min_box_area_ratio", 0.010)),
                ebook_image_min_width_ratio=float(self.config.get("ebook_image_min_width_ratio", 0.10)),
                ebook_image_min_height_ratio=float(self.config.get("ebook_image_min_height_ratio", 0.055)),
                ebook_image_color_fraction_min=float(self.config.get("ebook_image_color_fraction_min", 0.035)),
                ebook_image_nonwhite_fraction_min=float(self.config.get("ebook_image_nonwhite_fraction_min", 0.12)),
                ebook_image_edge_density_min=float(self.config.get("ebook_image_edge_density_min", 0.018)),
                ebook_image_texture_std_min=float(self.config.get("ebook_image_texture_std_min", 11.0)),
                ebook_image_padding_px=int(self.config.get("ebook_image_padding_px", 10)),
                ebook_image_guard_right_px=int(self.config.get("ebook_image_guard_right_px", 0)),
                ebook_image_guard_bottom_px=int(self.config.get("ebook_image_guard_bottom_px", 0)),
                ebook_cleanup_sample_exclude_image_margin_px=int(self.config.get("ebook_cleanup_sample_exclude_image_margin_px", 24)),
                ebook_auto_preset=bool(self.config.get("ebook_auto_preset", True)),
                ebook_auto_safe_ghost_strength=float(self.config.get("ebook_auto_safe_ghost_strength", 0.58)),
                ebook_auto_balanced_ghost_strength=float(self.config.get("ebook_auto_balanced_ghost_strength", 0.66)),
                ebook_auto_strong_ghost_strength=float(self.config.get("ebook_auto_strong_ghost_strength", 0.74)),
                ebook_auto_safe_sample_exclude_margin_px=int(self.config.get("ebook_auto_safe_sample_exclude_margin_px", 28)),
                ebook_auto_balanced_sample_exclude_margin_px=int(self.config.get("ebook_auto_balanced_sample_exclude_margin_px", 24)),
                ebook_auto_strong_sample_exclude_margin_px=int(self.config.get("ebook_auto_strong_sample_exclude_margin_px", 12)),
            )
        self._cached_engine_settings = (engine, settings)
        return self._cached_engine_settings

    def process_page(self, page_rgb: np.ndarray) -> np.ndarray:
        page_rgb = ensure_rgb_uint8(page_rgb)
        engine, settings = self._engine_and_settings()
        result = engine.process_page_rgb(page_rgb=page_rgb, settings=settings)
        return extract_image(result)


class LyProcessor(BaseProcessor):
    """Adapter for the uploaded IPCLASS/Lý engine.

    V7.7.7 replaces the previous duplicated fast adapter with the actual
    `watermaker IPCLASS.py` engine selected by the user.  The standalone GUI in
    that file is removed, but the image-processing functions are kept and loaded
    through `load_engine`, just like the other legacy engines.
    """

    mode = "ly"

    def _engine_and_params(self):
        cached = getattr(self, "_cached_engine_params", None)
        if cached is not None:
            return cached

        engine = load_engine(self.config.engine_path("ly"), "ly")

        keep_sat_lower = engine.parse_hsv_bound(
            str(self.config.get("keep_sat_lower", getattr(engine, "DEFAULT_KEEP_SAT_LOWER", "0,35,0")))
        )
        keep_sat_upper = engine.parse_hsv_bound(
            str(self.config.get("keep_sat_upper", getattr(engine, "DEFAULT_KEEP_SAT_UPPER", "179,255,255")))
        )
        preserve_illustrations = bool(self.config.get("preserve_illustrations", True))
        aggressive_white = bool(self.config.get("aggressive_white", False))
        background_saturation_upper = int(
            self.config.get(
                "background_saturation_upper",
                getattr(engine, "DEFAULT_BACKGROUND_SATURATION_UPPER", 65),
            )
        )
        graph_params = {
            "graph_preserve_enabled": bool(
                self.config.get(
                    "graph_preserve_enabled",
                    getattr(engine, "DEFAULT_GRAPH_PRESERVE_ENABLED", True),
                )
            ),
            "graph_faint_gray_max": int(
                self.config.get(
                    "graph_faint_gray_max",
                    getattr(engine, "DEFAULT_GRAPH_FAINT_GRAY_MAX", 238),
                )
            ),
            "graph_neutral_sat_max": int(
                self.config.get(
                    "graph_neutral_sat_max",
                    getattr(engine, "DEFAULT_GRAPH_NEUTRAL_SAT_MAX", 96),
                )
            ),
            "graph_local_contrast_min": int(
                self.config.get(
                    "graph_local_contrast_min",
                    getattr(engine, "DEFAULT_GRAPH_LOCAL_CONTRAST_MIN", 4),
                )
            ),
            "graph_canny_low": int(
                self.config.get(
                    "graph_canny_low",
                    getattr(engine, "DEFAULT_GRAPH_CANNY_LOW", 22),
                )
            ),
            "graph_canny_high": int(
                self.config.get(
                    "graph_canny_high",
                    getattr(engine, "DEFAULT_GRAPH_CANNY_HIGH", 88),
                )
            ),
            "graph_line_min_len": int(
                self.config.get(
                    "graph_line_min_len",
                    getattr(engine, "DEFAULT_GRAPH_LINE_MIN_LEN", 22),
                )
            ),
            "graph_dashed_close_px": int(
                self.config.get(
                    "graph_dashed_close_px",
                    getattr(engine, "DEFAULT_GRAPH_DASHED_CLOSE_PX", 15),
                )
            ),
            "graph_near_line_dilate_px": int(
                self.config.get(
                    "graph_near_line_dilate_px",
                    getattr(engine, "DEFAULT_GRAPH_NEAR_LINE_DILATE_PX", 24),
                )
            ),
            "graph_protect_dilate_px": int(
                self.config.get(
                    "graph_protect_dilate_px",
                    getattr(engine, "DEFAULT_GRAPH_PROTECT_DILATE_PX", 2),
                )
            ),
            "graph_pale_color_sat_min": int(
                self.config.get(
                    "graph_pale_color_sat_min",
                    getattr(engine, "DEFAULT_GRAPH_PALE_COLOR_SAT_MIN", 12),
                )
            ),
            "graph_pale_color_spread_min": int(
                self.config.get(
                    "graph_pale_color_spread_min",
                    getattr(engine, "DEFAULT_GRAPH_PALE_COLOR_SPREAD_MIN", 8),
                )
            ),
            "ly_watermark_cleanup_enabled": bool(
                self.config.get(
                    "ly_watermark_cleanup_enabled",
                    getattr(engine, "DEFAULT_LY_WATERMARK_CLEANUP_ENABLED", True),
                )
            ),
            "ly_watermark_gray_min": int(
                self.config.get(
                    "ly_watermark_gray_min",
                    getattr(engine, "DEFAULT_LY_WATERMARK_GRAY_MIN", 112),
                )
            ),
            "ly_watermark_gray_max": int(
                self.config.get(
                    "ly_watermark_gray_max",
                    getattr(engine, "DEFAULT_LY_WATERMARK_GRAY_MAX", 248),
                )
            ),
            "ly_watermark_sat_max": int(
                self.config.get(
                    "ly_watermark_sat_max",
                    getattr(engine, "DEFAULT_LY_WATERMARK_SAT_MAX", 135),
                )
            ),
            "ly_watermark_spread_max": int(
                self.config.get(
                    "ly_watermark_spread_max",
                    getattr(engine, "DEFAULT_LY_WATERMARK_SPREAD_MAX", 105),
                )
            ),
            "ly_watermark_dark_guard_gray": int(
                self.config.get(
                    "ly_watermark_dark_guard_gray",
                    getattr(engine, "DEFAULT_LY_WATERMARK_DARK_GUARD_GRAY", 135),
                )
            ),
            "ly_watermark_dilate_px": int(
                self.config.get(
                    "ly_watermark_dilate_px",
                    getattr(engine, "DEFAULT_LY_WATERMARK_DILATE_PX", 1),
                )
            ),
        }

        self._cached_engine_params = (
            engine,
            keep_sat_lower,
            keep_sat_upper,
            preserve_illustrations,
            aggressive_white,
            background_saturation_upper,
            graph_params,
        )
        return self._cached_engine_params

    def process_page(self, page_rgb: np.ndarray) -> np.ndarray:
        page_rgb = ensure_rgb_uint8(page_rgb)
        (
            engine,
            keep_sat_lower,
            keep_sat_upper,
            preserve_illustrations,
            aggressive_white,
            background_saturation_upper,
            graph_params,
        ) = self._engine_and_params()

        result = engine.process_page_rgb(
            page_rgb=page_rgb,
            keep_sat_lower=keep_sat_lower,
            keep_sat_upper=keep_sat_upper,
            preserve_illustrations=preserve_illustrations,
            aggressive_white=aggressive_white,
            background_saturation_upper=background_saturation_upper,
            **graph_params,
        )
        return extract_image(result)


class ToanProcessor(BaseProcessor):
    """Adapter for the legacy TDM engine, caching parsed settings per worker process."""

    mode = "toan"

    def _prepared(self):
        cached = getattr(self, "_cached_prepared", None)
        if cached is not None:
            return cached

        engine = load_engine(self.config.engine_path("toan"), "toan")
        if hasattr(engine, "create_settings_from_config"):
            settings = engine.create_settings_from_config(self.config.params)
        else:
            settings = engine.ProcessingSettings(
                top_template_path=self.config.get("top_template_path", "builtin"),
                bottom_template_path=self.config.get("bottom_template_path", "builtin"),
                diag_template_path=self.config.get("diag_template_path", "builtin"),
                top_threshold=float(self.config.get("top_threshold", 0.55)),
                bottom_threshold=float(self.config.get("bottom_threshold", 0.55)),
                diag_threshold=float(self.config.get("diag_threshold", 0.25)),
                protect_gray_threshold=int(self.config.get("protect_gray_threshold", 105)),
                min_matched_regions=int(self.config.get("min_matched_regions", 1)),
                header_height=str(self.config.get("header_height", "0%")),
                footer_height=str(self.config.get("footer_height", "0%")),
                output_grayscale=bool(self.config.get("output_grayscale", False)),
                graph_safe_paper_mode=bool(self.config.get("graph_safe_paper_mode", True)),
            )

        args_template = None
        if hasattr(engine, "_namespace_from_settings") and hasattr(engine, "clean_page_bgr"):
            args_template = engine._namespace_from_settings(settings)
            setattr(args_template, "_need_debug", False)

        self._cached_prepared = (engine, settings, args_template)
        return self._cached_prepared

    def _engine_and_settings(self):
        engine, settings, _args_template = self._prepared()
        return engine, settings

    def process_page(self, page_rgb: np.ndarray) -> np.ndarray:
        page_rgb = ensure_rgb_uint8(page_rgb)
        engine, settings, args_template = self._prepared()

        if args_template is not None:
            args = copy.copy(args_template)
            setattr(args, "_need_debug", False)
            bgr = cv2.cvtColor(page_rgb, cv2.COLOR_RGB2BGR)
            cleaned_bgr, _debug = engine.clean_page_bgr(bgr, args)
            return cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)

        result = engine.process_page_rgb(page_rgb=page_rgb, settings=settings)
        return extract_image(result)


class HoaProcessor(BaseProcessor):
    """Placeholder image-level adapter; Hóa/TYHH should run through stream PDF cleaning."""

    mode = "hoa"

    def process_page(self, page_rgb: np.ndarray) -> np.ndarray:
        page_rgb = ensure_rgb_uint8(page_rgb)
        load_engine(self.config.engine_path("hoa"), "hoa")
        raise NotImplementedError(
            "Hóa/TYHH dùng pipeline stream-only ở cấp PDF, không dùng xử lý ảnh từng trang."
        )


def process_ebook(page_rgb: np.ndarray, config: Mapping[str, Any] | ProcessorConfig | None = None) -> np.ndarray:
    return EbookProcessor(config).process_page(page_rgb)


def process_ly(page_rgb: np.ndarray, config: Mapping[str, Any] | ProcessorConfig | None = None) -> np.ndarray:
    return LyProcessor(config).process_page(page_rgb)


def process_toan(page_rgb: np.ndarray, config: Mapping[str, Any] | ProcessorConfig | None = None) -> np.ndarray:
    return ToanProcessor(config).process_page(page_rgb)


def process_hoa(page_rgb: np.ndarray, config: Mapping[str, Any] | ProcessorConfig | None = None) -> np.ndarray:
    return HoaProcessor(config).process_page(page_rgb)
