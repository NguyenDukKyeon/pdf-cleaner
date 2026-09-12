from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import json

from .defaults import DEFAULT_CONFIG, QUALITY_PRESETS

def deep_merge(default: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Merge current vào default nhưng vẫn giữ các key mới từ default."""
    out = deepcopy(default)

    def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                _merge(dst[k], v)
            else:
                dst[k] = v

    _merge(out, current)
    return out


class ConfigManager:
    """Đọc/ghi config.json."""

    def __init__(self, path: str | Path = "config.json"):
        self.path = Path(path)
        self.data: dict[str, Any] = deepcopy(DEFAULT_CONFIG)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.data = deepcopy(DEFAULT_CONFIG)
            self.save()
            return self.data

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config.json phải là object JSON.")
            self.data = deep_merge(DEFAULT_CONFIG, raw)
        except Exception:
            # Nếu config hỏng, không crash GUI; tạo lại file .broken backup.
            broken = self.path.with_suffix(".broken.json")
            try:
                broken.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
            self.data = deepcopy(DEFAULT_CONFIG)
            self.save()
        self._migrate_tdm_v7_defaults()
        return self.data

    def _migrate_tdm_v7_defaults(self) -> None:
        """Auto-migrate old TDM config so V7 ROI-cache modes are available."""
        modes = self.data.setdefault("modes", {})
        ly = modes.setdefault("ly", {})
        # V7.7.9: IPCLASS/Lý must assemble as full-raster PNG. The IPCLASS
        # preprocessor cleans whole-page background and text anti-aliasing;
        # hybrid overlays can leave the original PDF visible at patch edges and
        # create halos. This does not change the IPCLASS pixel algorithm.
        ly["output_mode"] = "full_raster"
        ly.setdefault("full_raster_format", "png")
        ly.setdefault("fallback_image_format", "png")

        # V890 + V8.9.4 port: strong Lý/IPCLASS preset and graph-safe guard.
        # Force critical values so existing config.json files do not keep the
        # older, weaker Lý defaults.
        ly["tailieuonthi_cleanup"] = "strong"
        ly["keep_sat_lower"] = "0,80,0"
        ly["preserve_illustrations"] = False
        ly.setdefault("aggressive_white", True)
        ly["ly_watermark_cleanup_enabled"] = True
        ly.setdefault("ly_watermark_gray_min", 112)
        ly.setdefault("ly_watermark_gray_max", 248)
        ly.setdefault("ly_watermark_sat_max", 135)
        ly.setdefault("ly_watermark_spread_max", 105)
        ly.setdefault("ly_watermark_dark_guard_gray", 135)
        ly.setdefault("ly_watermark_dilate_px", 1)
        ly.setdefault("graph_preserve_enabled", True)
        ly.setdefault("graph_faint_gray_max", 238)
        ly.setdefault("graph_neutral_sat_max", 96)
        ly.setdefault("graph_local_contrast_min", 4)
        ly.setdefault("graph_canny_low", 22)
        ly.setdefault("graph_canny_high", 88)
        ly.setdefault("graph_line_min_len", 22)
        ly.setdefault("graph_dashed_close_px", 15)
        ly.setdefault("graph_near_line_dilate_px", 24)
        ly.setdefault("graph_protect_dilate_px", 2)
        ly.setdefault("graph_pale_color_sat_min", 12)
        ly.setdefault("graph_pale_color_spread_min", 8)

        ebook = modes.setdefault("ebook", {})
        # V8.8.0: EBOOK cleanup uses rendered-page watermark removal. Hybrid
        # patch assembly can expose patch seams / dark diagonal scratches where
        # watermark crosses text and tables, so assemble EBOOK as lossless full
        # raster PNG while keeping the EBOOK image algorithm untouched.
        ebook["output_mode"] = "full_raster"
        ebook.setdefault("full_raster_format", "png")
        ebook.setdefault("fallback_image_format", "png")

        toan = modes.setdefault("toan", {})
        if toan.get("tdm_engine_version") != "v7.6.3_global_ruled_line":
            toan.update({
                "display_name": "Toán - TDM V7.6.3 Global Line",
                "output_grayscale": False,
                "graph_safe_paper_mode": True,
                "tdm_engine_version": "v7.6.3_global_ruled_line",
                "header_right_protect": True,
                "header_right_protect_x_min": 0.635,
                "header_right_protect_x_max": 0.995,
                "header_right_protect_y_min": 0.000,
                "header_right_protect_y_max": 0.048,
                "diag_band_width_px": 0,
                "diag_x_min": 0.50,
                "diag_x_max": 0.998,
                "diag_y_min": 0.48,
                "diag_y_max": 0.998,
                "line_restore": True,
                "line_restore_damaged_only": True,
                "line_restore_damage_gate_dilate_x": 72,
                "line_restore_damage_gate_dilate_y": 3,
                "line_restore_limit_to_diag_band": True,
                "line_evidence_baseline_kernel": 21,
                "line_evidence_min_contrast": 2,
                "line_evidence_close_x": 5,
                "line_evidence_y_radius": 1,
                "line_evidence_min_row_coverage": 0.18,
                "color_panel_preserve": True,
                "color_panel_protect_from_corridor": True,
                "color_panel_rebuild_fallback": False,
                "color_panel_patch_alpha": 0.92,
                "color_panel_local_patch": True,
                "color_panel_local_patch_dilate_x": 9,
                "color_panel_local_patch_dilate_y": 3,
                "mode": "balanced",
                "v7_panel_gate_min_pixels": 120,
                "v7_line_gate_min_rows": 18,
                "line_global_reconstruction": True,
                "line_global_full_page": False,
                "line_global_y_radius": 1,
                "line_global_blank_gray_min": 162,
                "line_global_blank_sat_max": 150,
                "line_global_blank_spread_max": 80,
                "line_global_missing_gray_min": 236,
                "line_global_missing_sat_max": 150,
                "line_global_missing_spread_max": 85,
                "line_global_damage_dilate_x": 420,
                "line_global_damage_dilate_y": 5,
                "line_global_text_gray_max": 170,
                "line_global_colored_ink_sat_min": 36,
                "line_global_colored_ink_gray_max": 238,
                "line_global_edge_canny_low": 24,
                "line_global_edge_canny_high": 96,
                "line_global_edge_gray_max": 160,
                "line_global_fill_gray_min": 135,
                "line_global_fill_sat_min": 12,
                "line_global_fill_spread_min": 5,
                "line_global_fill_close_x": 11,
                "line_global_fill_close_y": 7,
                "line_global_guard_dilate_x": 5,
                "line_global_guard_dilate_y": 3,
                "line_global_guard_min_area": 2,
                "line_global_min_run_px": 2,
                "line_restore_scope": "watermark_roi_only",
                "watermark_roi_background_reconstruct": True,
                "watermark_roi_bg_full_band": True,
                "watermark_roi_bg_expand_x": 28,
                "watermark_roi_bg_expand_y": 8,
                "watermark_roi_bg_line_erase_y_radius": 1,
                "watermark_roi_bg_content_dilate_x": 5,
                "watermark_roi_bg_content_dilate_y": 3,
                "watermark_roi_bg_content_gray_max": 155,
                "watermark_roi_bg_content_sat_min": 42,
                "watermark_roi_bg_gray_min": 176,
                "watermark_roi_bg_sat_max": 160,
                "watermark_roi_bg_spread_max": 95,
                "watermark_roi_bg_alpha": 1.0,
                "hybrid_force_watermark_roi_patch": True,
                "hybrid_watermark_roi_patch_width_px": 82,
            "safe_text_aa_enable": True,
            "safe_text_aa_gray_max": 145,
            "safe_text_aa_min_contrast": 42,
            "safe_text_aa_local_kernel": 31,
            "safe_text_aa_component_min_area": 2,
            "safe_text_aa_component_max_area": 25000,
            "safe_text_aa_dilate": 1,
            })
            try:
                self.save()
            except Exception:
                pass

    def apply_preset(self, preset_name: str) -> dict[str, Any]:
        """Apply a named quality preset to the common config and persist it."""
        key = str(preset_name).strip().lower()
        if key not in QUALITY_PRESETS:
            raise ValueError(f"Preset không hợp lệ: {preset_name}. Hợp lệ: {sorted(QUALITY_PRESETS)}")
        common = self.get_common()
        common.update(QUALITY_PRESETS[key])
        common["quality_profile"] = key
        self.save()
        return common

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def reset(self) -> dict[str, Any]:
        self.data = deepcopy(DEFAULT_CONFIG)
        self.save()
        return self.data

    def get_common(self) -> dict[str, Any]:
        return self.data.setdefault("common", {})

    def get_modes(self) -> dict[str, Any]:
        return self.data.setdefault("modes", {})

    def get_mode_config(self, mode: str) -> dict[str, Any]:
        return self.get_modes().setdefault(mode, {})

    def update_common(self, values: dict[str, Any]) -> None:
        self.get_common().update(values)
        self.save()

    def update_mode(self, mode: str, values: dict[str, Any]) -> None:
        self.get_mode_config(mode).update(values)
        self.save()
