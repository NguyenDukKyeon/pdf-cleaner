from __future__ import annotations

import concurrent.futures
import multiprocessing as mp
import os
import queue
import shutil
import tempfile
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

import cv2
try:
    import img2pdf
except Exception:  # optional for legacy process_pdf; main.py does not need it
    img2pdf = None
import numpy as np
from PIL import Image
from pdf2image import convert_from_path, pdfinfo_from_path



# ============================================================
# Scan Document OCR Preprocessor GUI
# - Adaptive Otsu Thresholding on HSV Value channel
# - Fixed Border Block Header/Footer White-out
# - Multiprocessing PDF page pipeline
# - No Gaussian Blur
# - No Inpainting
# ============================================================

# OpenCV HSV ranges:
# H: 0..179
# S: 0..255
# V: 0..255

DEFAULT_KEEP_SAT_LOWER = "0,35,0"
DEFAULT_KEEP_SAT_UPPER = "179,255,255"

# Các giá trị bên dưới được giữ lại để tương thích GUI cũ.
# Bộ lọc chữ tối và nền sáng/nhạt hiện được tự động hóa bằng Otsu trên kênh V.
DEFAULT_KEEP_DARK_LOWER = "AUTO"
DEFAULT_KEEP_DARK_UPPER = "AUTO"
DEFAULT_REMOVE_LIGHT_LOWER = "AUTO"
DEFAULT_REMOVE_LIGHT_UPPER = "AUTO"

DEFAULT_HEADER_HEIGHT = "3%"
DEFAULT_FOOTER_HEIGHT = "3%"

DEFAULT_BACKGROUND_SATURATION_UPPER = 65

# V8.9.3: bảo vệ đồ thị/lưới mảnh cho mode Lý/IPCLASS.
# IPCLASS làm trắng nền bằng Otsu + saturation gate; điều này an toàn cho chữ
# đậm nhưng có thể làm mất đường trục, đường lưới và đường cong xám nhạt trong
# đồ thị Vật lý. Các ngưỡng bên dưới chỉ thêm mask bảo vệ, không làm tối/nhòe
# ảnh và không thay đổi cơ chế xóa nền chính.
DEFAULT_GRAPH_PRESERVE_ENABLED = True
DEFAULT_GRAPH_FAINT_GRAY_MAX = 238
DEFAULT_GRAPH_NEUTRAL_SAT_MAX = 96
DEFAULT_GRAPH_LOCAL_CONTRAST_MIN = 4
DEFAULT_GRAPH_CANNY_LOW = 22
DEFAULT_GRAPH_CANNY_HIGH = 88
DEFAULT_GRAPH_LINE_MIN_LEN = 22
DEFAULT_GRAPH_DASHED_CLOSE_PX = 15
DEFAULT_GRAPH_NEAR_LINE_DILATE_PX = 24
DEFAULT_GRAPH_PROTECT_DILATE_PX = 2
DEFAULT_GRAPH_PALE_COLOR_SAT_MIN = 12
DEFAULT_GRAPH_PALE_COLOR_SPREAD_MIN = 8

# V8.9.4: Lý/IPCLASS strong neutral watermark cleanup. This removes the
# gray/gray-blue MAP STUDY / TaiLieuOnThi stamps while keeping dark text, vivid
# colored ink and graph strokes. It is image-level only; no blur/inpaint.
DEFAULT_LY_WATERMARK_CLEANUP_ENABLED = True
DEFAULT_LY_WATERMARK_GRAY_MIN = 112
DEFAULT_LY_WATERMARK_GRAY_MAX = 248
DEFAULT_LY_WATERMARK_SAT_MAX = 135
DEFAULT_LY_WATERMARK_SPREAD_MAX = 105
DEFAULT_LY_WATERMARK_DARK_GUARD_GRAY = 135
DEFAULT_LY_WATERMARK_DILATE_PX = 1


@dataclass(frozen=True)
class ProcessingSettings:
    """Nhóm thiết lập xử lý có thể pickle để gửi sang tiến trình con."""

    keep_sat_lower: tuple[int, int, int]
    keep_sat_upper: tuple[int, int, int]
    preserve_illustrations: bool
    aggressive_white: bool
    header_height: str
    footer_height: str
    background_saturation_upper: int = DEFAULT_BACKGROUND_SATURATION_UPPER
    graph_preserve_enabled: bool = DEFAULT_GRAPH_PRESERVE_ENABLED
    graph_faint_gray_max: int = DEFAULT_GRAPH_FAINT_GRAY_MAX
    graph_neutral_sat_max: int = DEFAULT_GRAPH_NEUTRAL_SAT_MAX
    graph_local_contrast_min: int = DEFAULT_GRAPH_LOCAL_CONTRAST_MIN
    graph_canny_low: int = DEFAULT_GRAPH_CANNY_LOW
    graph_canny_high: int = DEFAULT_GRAPH_CANNY_HIGH
    graph_line_min_len: int = DEFAULT_GRAPH_LINE_MIN_LEN
    graph_dashed_close_px: int = DEFAULT_GRAPH_DASHED_CLOSE_PX
    graph_near_line_dilate_px: int = DEFAULT_GRAPH_NEAR_LINE_DILATE_PX
    graph_protect_dilate_px: int = DEFAULT_GRAPH_PROTECT_DILATE_PX
    graph_pale_color_sat_min: int = DEFAULT_GRAPH_PALE_COLOR_SAT_MIN
    graph_pale_color_spread_min: int = DEFAULT_GRAPH_PALE_COLOR_SPREAD_MIN
    ly_watermark_cleanup_enabled: bool = DEFAULT_LY_WATERMARK_CLEANUP_ENABLED
    ly_watermark_gray_min: int = DEFAULT_LY_WATERMARK_GRAY_MIN
    ly_watermark_gray_max: int = DEFAULT_LY_WATERMARK_GRAY_MAX
    ly_watermark_sat_max: int = DEFAULT_LY_WATERMARK_SAT_MAX
    ly_watermark_spread_max: int = DEFAULT_LY_WATERMARK_SPREAD_MAX
    ly_watermark_dark_guard_gray: int = DEFAULT_LY_WATERMARK_DARK_GUARD_GRAY
    ly_watermark_dilate_px: int = DEFAULT_LY_WATERMARK_DILATE_PX


@dataclass(frozen=True)
class PageTask:
    """Thông tin của một trang PDF cần xử lý trong tiến trình con."""

    pdf_path: str
    output_png_path: str
    page_index: int
    total_pages: int
    dpi: int
    poppler_path: str | None
    debug_dir: str | None
    settings: ProcessingSettings


@dataclass(frozen=True)
class PageResult:
    """Kết quả trả về từ tiến trình con cho một trang PDF."""

    page_index: int
    success: bool
    output_png_path: str | None
    header_px: int = 0
    footer_px: int = 0
    otsu_v_threshold: int | None = None
    whiteout_mode: str = ""
    error: str | None = None


def parse_hsv_bound(value: str) -> np.ndarray:
    """
    Parse HSV bound dạng 'H,S,V' thành np.array([H, S, V]).

    OpenCV HSV:
    - H: 0..179
    - S: 0..255
    - V: 0..255
    """
    value = value.strip()
    if value.upper() == "AUTO":
        raise ValueError("Trường này đang ở chế độ AUTO, không cần nhập HSV thủ công.")

    parts = [int(x.strip()) for x in value.split(",")]

    if len(parts) != 3:
        raise ValueError("HSV phải có dạng H,S,V. Ví dụ: 0,35,0")

    h, s, v = parts

    if not (0 <= h <= 179):
        raise ValueError("H phải nằm trong [0, 179].")
    if not (0 <= s <= 255):
        raise ValueError("S phải nằm trong [0, 255].")
    if not (0 <= v <= 255):
        raise ValueError("V phải nằm trong [0, 255].")

    return np.array([h, s, v], dtype=np.uint8)


def parse_edge_height(value: str, image_height: int) -> int:
    """
    Parse chiều cao header/footer.

    Hỗ trợ 3 kiểu nhập:
    - Pixel: "120"
    - Phần trăm: "8%" nghĩa là 8% chiều cao trang
    - Tỉ lệ: "0.08" nghĩa là 8% chiều cao trang
    """
    value = value.strip()

    if not value:
        return 0

    if value.endswith("%"):
        percent = float(value[:-1].strip())
        pixels = int(image_height * percent / 100.0)
    else:
        number = float(value)

        if 0 < number < 1:
            pixels = int(image_height * number)
        else:
            pixels = int(number)

    return max(0, min(pixels, image_height))


def hsv_tuple_to_array(value: tuple[int, int, int]) -> np.ndarray:
    """Chuyển tuple HSV pickle-friendly thành np.ndarray cho OpenCV."""
    return np.array(value, dtype=np.uint8)


def mask_from_bool(boolean_mask: np.ndarray) -> np.ndarray:
    """Chuyển mask boolean sang mask uint8 0/255."""
    return boolean_mask.astype(np.uint8) * 255


def put_process_log(process_log_queue: Any, message: str) -> None:
    """Gửi log từ tiến trình con về queue multiprocessing-safe, nếu có."""
    if process_log_queue is None:
        return

    try:
        process_log_queue.put(message)
    except Exception:
        # Không để lỗi ghi log làm hỏng xử lý trang.
        pass


def drain_process_log_queue(process_log_queue: Any, log: Callable[[str], None]) -> None:
    """Chuyển log từ multiprocessing queue sang queue Tkinter của GUI."""
    if process_log_queue is None:
        return

    while True:
        try:
            message = process_log_queue.get_nowait()
        except Exception:
            break
        log(message)


def build_adaptive_value_masks(
    hsv: np.ndarray,
    *,
    background_saturation_upper: int = DEFAULT_BACKGROUND_SATURATION_UPPER,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """
    Tự động tạo mask chữ/nét tối và mask nền sáng/nhạt bằng Otsu trên kênh V.

    Khác với bản cũ:
    - Không dùng ngưỡng V tĩnh kiểu 185..255 để xóa nền.
    - Mỗi trang tự tính một ngưỡng V riêng bằng Otsu.
    - Kênh S chỉ còn vai trò "gác cổng" để tránh xóa nhầm vùng màu/sơ đồ.
    """
    if hsv.ndim != 3 or hsv.shape[2] != 3:
        raise ValueError("Ảnh HSV không hợp lệ.")

    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    # Otsu tự tìm ngưỡng phân tách tối/sáng trên từng trang.
    otsu_threshold_float, _ = cv2.threshold(
        value,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    otsu_threshold = int(round(otsu_threshold_float))

    # Nếu trang gần như trắng tuyệt đối, Otsu thường trả về 0.
    # Điều này vẫn ổn: không có nhiều vùng tối cần bảo vệ.
    dark_mask = mask_from_bool(value <= otsu_threshold)
    light_value_mask = mask_from_bool(value > otsu_threshold)

    low_saturation_mask = mask_from_bool(
        saturation <= int(np.clip(background_saturation_upper, 0, 255))
    )

    light_noise_mask = cv2.bitwise_and(light_value_mask, low_saturation_mask)

    return dark_mask, light_value_mask, light_noise_mask, otsu_threshold


def build_illustration_preserve_mask(
    protected_mask: np.ndarray,
    page_shape: tuple[int, int, int],
    min_area_ratio: float = 0.008,
    max_area_ratio: float = 0.70,
    min_width: int = 80,
    min_height: int = 60,
    padding: int = 8,
    close_kernel_size: int = 13,
) -> np.ndarray:
    """
    Bảo vệ vùng ảnh/sơ đồ lớn.

    Ý tưởng:
    - Dựa trên vùng đã được bảo vệ ban đầu: màu bão hòa hoặc pixel tối.
    - Dùng morphology trên MASK, không làm mờ ảnh gốc.
    - Tìm contour lớn để suy ra bounding box của sơ đồ/ảnh.
    - Bảo vệ cả bounding box để tránh mất chi tiết sáng trong ảnh minh họa.
    """
    height, width = protected_mask.shape
    page_area = height * width

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (close_kernel_size, close_kernel_size),
    )

    connected = cv2.morphologyEx(
        protected_mask,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1,
    )

    contours, _ = cv2.findContours(
        connected,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    preserve_mask = np.zeros((height, width), dtype=np.uint8)

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        box_area = w * h
        area_ratio = box_area / page_area

        if area_ratio < min_area_ratio:
            continue
        if area_ratio > max_area_ratio:
            continue
        if w < min_width or h < min_height:
            continue

        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(width, x + w + padding)
        y2 = min(height, y + h + padding)

        cv2.rectangle(
            preserve_mask,
            (x1, y1),
            (x2, y2),
            color=255,
            thickness=-1,
        )

    return preserve_mask


def _component_filter_mask(
    source_mask: np.ndarray,
    *,
    min_area: int = 2,
    max_area_ratio: float = 0.035,
    min_span: int = 3,
) -> np.ndarray:
    """Keep small/medium stroke components and reject page-scale noise."""
    height, width = source_mask.shape[:2]
    page_area = max(1, height * width)
    max_area = max(min_area, int(round(page_area * max_area_ratio)))
    output = np.zeros((height, width), dtype=np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        (source_mask > 0).astype(np.uint8),
        connectivity=8,
    )

    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < min_area or area > max_area:
            continue
        if max(w, h) < min_span:
            continue
        output[labels == label] = 255
    return output


def _keep_components_touching_gate(source_mask: np.ndarray, gate_mask: np.ndarray) -> np.ndarray:
    """Keep full connected components from source_mask if they touch gate_mask."""
    output = np.zeros_like(source_mask, dtype=np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        (source_mask > 0).astype(np.uint8),
        connectivity=8,
    )
    gate_bool = gate_mask > 0
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area <= 0:
            continue
        component_slice = labels[y : y + h, x : x + w] == label
        if np.any(component_slice & gate_bool[y : y + h, x : x + w]):
            output[y : y + h, x : x + w][component_slice] = 255
    return output


def _filter_graph_line_components(
    source_mask: np.ndarray,
    *,
    orientation: str,
    min_len: int,
    page_width: int,
    page_height: int,
) -> np.ndarray:
    """Keep only compact, ruler-like grid/axis components.

    Long answer guide lines and small watermark letters both create horizontal /
    vertical fragments. Graph grid lines are different: they are compact relative
    to the page but long inside the local graph box, and have a very high aspect
    ratio. This helper keeps those strokes and rejects most watermark glyphs.
    """
    out = np.zeros_like(source_mask, dtype=np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        (source_mask > 0).astype(np.uint8),
        connectivity=8,
    )
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area <= 0:
            continue
        if orientation == "h":
            if w < min_len:
                continue
            if w > int(round(page_width * 0.38)):
                continue
            if h > max(10, int(round(page_height * 0.010))):
                continue
            if w < 5 * max(1, h):
                continue
        else:
            if h < min_len:
                continue
            if h > int(round(page_height * 0.28)):
                continue
            if w > max(10, int(round(page_width * 0.012))):
                continue
            if h < 5 * max(1, w):
                continue
        out[labels == label] = 255
    return out


def _build_compact_blue_graph_roi(
    blue_mask: np.ndarray,
    horizontal: np.ndarray,
    vertical: np.ndarray,
    *,
    min_line_len: int,
) -> np.ndarray:
    """Locate compact graph boxes using blue graph ink plus H/V grid evidence."""
    height, width = blue_mask.shape[:2]
    roi = np.zeros((height, width), dtype=np.uint8)

    # Connect axes/curves inside one graph without joining full-width dotted
    # answer guide lines. The later bbox filter rejects full-width components.
    seed = cv2.morphologyEx(
        blue_mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)),
        iterations=1,
    )
    seed = cv2.dilate(
        seed,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=1,
    )

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        (seed > 0).astype(np.uint8),
        connectivity=8,
    )

    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < 25:
            continue
        if w < max(40, int(round(min_line_len * 0.75))) or h < max(24, int(round(min_line_len * 0.35))):
            continue
        if w > int(round(width * 0.40)) or h > int(round(height * 0.30)):
            continue

        pad = max(22, int(round(min_line_len * 0.35)))
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(width, x + w + pad)
        y2 = min(height, y + h + pad)

        hpx = int(np.count_nonzero(horizontal[y1:y2, x1:x2]))
        vpx = int(np.count_nonzero(vertical[y1:y2, x1:x2]))
        if hpx < max(70, min_line_len) or vpx < max(55, int(round(min_line_len * 0.75))):
            continue

        h_local = cv2.dilate(
            horizontal[y1:y2, x1:x2],
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5)),
            iterations=1,
        )
        v_local = cv2.dilate(
            vertical[y1:y2, x1:x2],
            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)),
            iterations=1,
        )
        intersections = int(np.count_nonzero(cv2.bitwise_and(h_local, v_local)))
        if intersections < max(24, int(round(min_line_len * 0.35))):
            continue

        roi[y1:y2, x1:x2] = 255

    return roi


def build_graph_preserve_mask(
    bgr: np.ndarray,
    hsv: np.ndarray,
    *,
    otsu_threshold: int,
    enabled: bool = DEFAULT_GRAPH_PRESERVE_ENABLED,
    faint_gray_max: int = DEFAULT_GRAPH_FAINT_GRAY_MAX,
    neutral_sat_max: int = DEFAULT_GRAPH_NEUTRAL_SAT_MAX,
    local_contrast_min: int = DEFAULT_GRAPH_LOCAL_CONTRAST_MIN,
    canny_low: int = DEFAULT_GRAPH_CANNY_LOW,
    canny_high: int = DEFAULT_GRAPH_CANNY_HIGH,
    line_min_len: int = DEFAULT_GRAPH_LINE_MIN_LEN,
    dashed_close_px: int = DEFAULT_GRAPH_DASHED_CLOSE_PX,
    near_line_dilate_px: int = DEFAULT_GRAPH_NEAR_LINE_DILATE_PX,
    protect_dilate_px: int = DEFAULT_GRAPH_PROTECT_DILATE_PX,
    pale_color_sat_min: int = DEFAULT_GRAPH_PALE_COLOR_SAT_MIN,
    pale_color_spread_min: int = DEFAULT_GRAPH_PALE_COLOR_SPREAD_MIN,
) -> np.ndarray:
    """
    Detect and preserve graph strokes before strong IPCLASS whitening.

    V8.9.4 changes the old global H/V-line guard into a compact blue-anchored
    graph guard. This prevents the repeated MAP STUDY diagonal stamps and full
    page dotted answer lines from being protected as if they were graph content.
    """
    if not enabled:
        return np.zeros(hsv.shape[:2], dtype=np.uint8)
    if bgr.ndim != 3 or hsv.ndim != 3:
        return np.zeros(hsv.shape[:2], dtype=np.uint8)

    height, width = hsv.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    faint_gray_max = int(np.clip(faint_gray_max, 0, 255))
    neutral_sat_max = int(np.clip(neutral_sat_max, 0, 255))
    local_contrast_min = max(0, int(local_contrast_min))
    dashed_close_px = max(3, int(dashed_close_px))
    near_line_dilate_px = max(0, int(near_line_dilate_px))
    protect_dilate_px = max(0, int(protect_dilate_px))
    pale_color_sat_min = int(np.clip(pale_color_sat_min, 0, 255))
    pale_color_spread_min = max(0, int(pale_color_spread_min))

    # Scale minimum grid-line length with DPI/page size. This is the key filter
    # that rejects watermark glyph fragments while keeping actual graph grids.
    scaled_min_len = max(int(line_min_len), max(55, int(round(min(height, width) * 0.075))))

    median_kernel = 31 if min(height, width) >= 900 else 21
    if median_kernel % 2 == 0:
        median_kernel += 1
    local_bg = cv2.medianBlur(gray, median_kernel)
    local_contrast = local_bg.astype(np.int16) - gray.astype(np.int16)

    edge = cv2.Canny(
        gray,
        int(np.clip(canny_low, 0, 255)),
        int(np.clip(max(canny_high, canny_low + 1), 1, 255)),
    )
    otsu_floor = min(255, max(0, int(otsu_threshold) + 2))
    faint_stroke_bool = (
        (value <= min(242, faint_gray_max + 4))
        & (value >= otsu_floor)
        & (saturation <= min(120, neutral_sat_max + 24))
        & ((local_contrast >= max(2, local_contrast_min - 1)) | (edge > 0))
    )
    faint_stroke_mask = mask_from_bool(faint_stroke_bool)

    h_close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (dashed_close_px, 1))
    v_close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, dashed_close_px))
    h_line_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (scaled_min_len, 1))
    v_line_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, scaled_min_len))

    horizontal = cv2.morphologyEx(faint_stroke_mask, cv2.MORPH_CLOSE, h_close_kernel, iterations=1)
    horizontal = cv2.morphologyEx(horizontal, cv2.MORPH_OPEN, h_line_kernel, iterations=1)
    horizontal = _filter_graph_line_components(
        horizontal,
        orientation="h",
        min_len=scaled_min_len,
        page_width=width,
        page_height=height,
    )

    vertical = cv2.morphologyEx(faint_stroke_mask, cv2.MORPH_CLOSE, v_close_kernel, iterations=1)
    vertical = cv2.morphologyEx(vertical, cv2.MORPH_OPEN, v_line_kernel, iterations=1)
    vertical = _filter_graph_line_components(
        vertical,
        orientation="v",
        min_len=scaled_min_len,
        page_width=width,
        page_height=height,
    )

    bgr_spread = bgr.max(axis=2).astype(np.int16) - bgr.min(axis=2).astype(np.int16)
    rgb_view = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    blue_dominance = rgb_view[:, :, 2].astype(np.int16) - rgb_view[:, :, 0].astype(np.int16)
    blue_mask = mask_from_bool(
        (
            (saturation >= max(45, pale_color_sat_min + 30))
            & (bgr_spread >= max(35, pale_color_spread_min + 27))
            & (value >= 80)
            & (hsv[:, :, 0] >= 85)
            & (hsv[:, :, 0] <= 145)
        )
        | (
            (blue_dominance >= 25)
            & (saturation >= 45)
            & (bgr_spread >= 35)
            & (value >= 80)
        )
    )

    graph_roi = _build_compact_blue_graph_roi(
        blue_mask,
        horizontal,
        vertical,
        min_line_len=scaled_min_len,
    )
    if np.count_nonzero(graph_roi) <= 0:
        return np.zeros((height, width), dtype=np.uint8)

    line_mask = cv2.bitwise_and(cv2.bitwise_or(horizontal, vertical), graph_roi)
    graph_roi_dilated = cv2.dilate(
        graph_roi,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=1,
    )

    # For the final protected graph ink, require a stronger blue core and then
    # dilate it a little. This keeps antialiased axes/curves but avoids protecting
    # pale gray-blue watermark letters that merely share the same hue.
    strong_blue_mask = mask_from_bool(
        (
            (saturation >= 95)
            & (bgr_spread >= 85)
            & (value >= 80)
            & (hsv[:, :, 0] >= 85)
            & (hsv[:, :, 0] <= 145)
        )
        | (
            (blue_dominance >= 55)
            & (saturation >= 80)
            & (bgr_spread >= 75)
            & (value >= 80)
        )
    )
    strong_blue_mask = cv2.dilate(
        strong_blue_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    )
    blue_graph_mask = cv2.bitwise_and(strong_blue_mask, graph_roi_dilated)
    dark_graph_mask = mask_from_bool((gray <= 110) & (graph_roi_dilated > 0))

    graph_mask = cv2.bitwise_or(line_mask, blue_graph_mask)
    graph_mask = cv2.bitwise_or(graph_mask, dark_graph_mask)

    if protect_dilate_px > 0:
        dilate_size = max(3, protect_dilate_px * 2 + 1)
        graph_mask = cv2.dilate(
            graph_mask,
            cv2.getStructuringElement(cv2.MORPH_RECT, (dilate_size, dilate_size)),
            iterations=1,
        )

    return graph_mask


def build_ly_neutral_watermark_remove_mask(
    bgr: np.ndarray,
    hsv: np.ndarray,
    graph_mask: np.ndarray,
    *,
    enabled: bool = DEFAULT_LY_WATERMARK_CLEANUP_ENABLED,
    gray_min: int = DEFAULT_LY_WATERMARK_GRAY_MIN,
    gray_max: int = DEFAULT_LY_WATERMARK_GRAY_MAX,
    sat_max: int = DEFAULT_LY_WATERMARK_SAT_MAX,
    spread_max: int = DEFAULT_LY_WATERMARK_SPREAD_MAX,
    dark_guard_gray: int = DEFAULT_LY_WATERMARK_DARK_GUARD_GRAY,
    dilate_px: int = DEFAULT_LY_WATERMARK_DILATE_PX,
) -> np.ndarray:
    """Mask gray/gray-blue watermark pixels for whitening, excluding real content."""
    if not enabled:
        return np.zeros(hsv.shape[:2], dtype=np.uint8)
    if bgr.ndim != 3 or hsv.ndim != 3:
        return np.zeros(hsv.shape[:2], dtype=np.uint8)

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    spread = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)

    gray_min = int(np.clip(gray_min, 0, 255))
    gray_max = int(np.clip(gray_max, 0, 255))
    sat_max = int(np.clip(sat_max, 0, 255))
    spread_max = max(0, int(spread_max))
    dark_guard_gray = int(np.clip(dark_guard_gray, 0, 255))
    dilate_px = max(0, int(dilate_px))

    # Main watermark candidates: light/mid neutral or gray-blue strokes.  The
    # lower bound is lifted above dark_guard_gray so isolated math symbols, minus
    # signs and text antialiasing are kept.
    candidate_gray_min = max(gray_min, min(255, dark_guard_gray + 1))
    candidate = (
        (gray >= candidate_gray_min)
        & (gray <= gray_max)
        & (value >= candidate_gray_min)
        & ((saturation <= sat_max) | (spread <= spread_max))
    )

    # Preserve dark text/formulas and a small antialias halo around them.
    dark_core = mask_from_bool(gray <= dark_guard_gray)
    dark_guard = cv2.dilate(
        dark_core,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    ) > 0

    # Preserve true colored ink. Pale gray-blue watermark often has low spread;
    # blue/red text, answer guide lines and graph curves have much stronger color
    # separation and/or saturation.
    colored_guard = (
        ((saturation >= 105) & (spread >= 75))
        | ((saturation >= 80) & (spread >= 110))
        | ((saturation >= 60) & (spread >= 150))
    )

    graph_guard = cv2.dilate(
        graph_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=1,
    ) > 0

    remove = candidate & (~dark_guard) & (~colored_guard) & (~graph_guard)
    remove_mask = mask_from_bool(remove)
    remove_mask = cv2.morphologyEx(
        remove_mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    )
    if dilate_px > 0:
        k = dilate_px * 2 + 1
        remove_mask = cv2.dilate(
            remove_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)),
            iterations=1,
        )

    # Re-apply hard guards after morphology expansion.
    hard_guard = dark_guard | colored_guard | graph_guard | (gray <= dark_guard_gray)
    remove_mask[hard_guard] = 0
    return remove_mask


def process_page_rgb(
    page_rgb: np.ndarray,
    *,
    keep_sat_lower: np.ndarray,
    keep_sat_upper: np.ndarray,
    preserve_illustrations: bool = True,
    aggressive_white: bool = False,
    background_saturation_upper: int = DEFAULT_BACKGROUND_SATURATION_UPPER,
    graph_preserve_enabled: bool = DEFAULT_GRAPH_PRESERVE_ENABLED,
    graph_faint_gray_max: int = DEFAULT_GRAPH_FAINT_GRAY_MAX,
    graph_neutral_sat_max: int = DEFAULT_GRAPH_NEUTRAL_SAT_MAX,
    graph_local_contrast_min: int = DEFAULT_GRAPH_LOCAL_CONTRAST_MIN,
    graph_canny_low: int = DEFAULT_GRAPH_CANNY_LOW,
    graph_canny_high: int = DEFAULT_GRAPH_CANNY_HIGH,
    graph_line_min_len: int = DEFAULT_GRAPH_LINE_MIN_LEN,
    graph_dashed_close_px: int = DEFAULT_GRAPH_DASHED_CLOSE_PX,
    graph_near_line_dilate_px: int = DEFAULT_GRAPH_NEAR_LINE_DILATE_PX,
    graph_protect_dilate_px: int = DEFAULT_GRAPH_PROTECT_DILATE_PX,
    graph_pale_color_sat_min: int = DEFAULT_GRAPH_PALE_COLOR_SAT_MIN,
    graph_pale_color_spread_min: int = DEFAULT_GRAPH_PALE_COLOR_SPREAD_MIN,
    ly_watermark_cleanup_enabled: bool = DEFAULT_LY_WATERMARK_CLEANUP_ENABLED,
    ly_watermark_gray_min: int = DEFAULT_LY_WATERMARK_GRAY_MIN,
    ly_watermark_gray_max: int = DEFAULT_LY_WATERMARK_GRAY_MAX,
    ly_watermark_sat_max: int = DEFAULT_LY_WATERMARK_SAT_MAX,
    ly_watermark_spread_max: int = DEFAULT_LY_WATERMARK_SPREAD_MAX,
    ly_watermark_dark_guard_gray: int = DEFAULT_LY_WATERMARK_DARK_GUARD_GRAY,
    ly_watermark_dilate_px: int = DEFAULT_LY_WATERMARK_DILATE_PX,
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, int]]:
    """
    Tiền xử lý một trang ảnh RGB.

    Logic nâng cấp:
    1. RGB -> BGR -> HSV.
    2. Tạo mask bảo vệ:
       - pixel màu bão hòa;
       - chữ/nét tối được nhận diện tự động bằng Otsu trên kênh V.
    3. Tạo mask xóa nền:
       - pixel sáng theo ngưỡng V tự động;
       - ưu tiên vùng ít bão hòa màu để tránh xóa sơ đồ/ảnh.
    4. Bitwise:
       - giữ pixel gốc ở vùng cần bảo vệ;
       - thay pixel nền nhiễu bằng trắng.

    Không làm nhòe chữ.
    Không inpainting.
    Không Gaussian Blur.
    """
    if page_rgb.ndim != 3 or page_rgb.shape[2] != 3:
        raise ValueError("page_rgb phải là ảnh RGB 3 kênh.")

    bgr = cv2.cvtColor(page_rgb, cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    saturated_mask = cv2.inRange(hsv, keep_sat_lower, keep_sat_upper)

    dark_mask, light_value_mask, light_noise_mask, otsu_threshold = build_adaptive_value_masks(
        hsv,
        background_saturation_upper=background_saturation_upper,
    )

    protected_mask = cv2.bitwise_or(saturated_mask, dark_mask)

    graph_mask = build_graph_preserve_mask(
        bgr=bgr,
        hsv=hsv,
        otsu_threshold=otsu_threshold,
        enabled=graph_preserve_enabled,
        faint_gray_max=graph_faint_gray_max,
        neutral_sat_max=graph_neutral_sat_max,
        local_contrast_min=graph_local_contrast_min,
        canny_low=graph_canny_low,
        canny_high=graph_canny_high,
        line_min_len=graph_line_min_len,
        dashed_close_px=graph_dashed_close_px,
        near_line_dilate_px=graph_near_line_dilate_px,
        protect_dilate_px=graph_protect_dilate_px,
        pale_color_sat_min=graph_pale_color_sat_min,
        pale_color_spread_min=graph_pale_color_spread_min,
    )
    watermark_remove_mask = build_ly_neutral_watermark_remove_mask(
        bgr=bgr,
        hsv=hsv,
        graph_mask=graph_mask,
        enabled=ly_watermark_cleanup_enabled,
        gray_min=ly_watermark_gray_min,
        gray_max=ly_watermark_gray_max,
        sat_max=ly_watermark_sat_max,
        spread_max=ly_watermark_spread_max,
        dark_guard_gray=ly_watermark_dark_guard_gray,
        dilate_px=ly_watermark_dilate_px,
    )

    # Remove watermark candidates from all protection classes, then re-add the
    # graph guard. This lets aggressive mode whiten MAP STUDY/TaiLieuOnThi while
    # still preserving grid/axis/curve strokes.
    if np.count_nonzero(watermark_remove_mask) > 0:
        protected_mask[watermark_remove_mask > 0] = 0
    protected_mask = cv2.bitwise_or(protected_mask, graph_mask)

    if preserve_illustrations:
        illustration_mask = build_illustration_preserve_mask(
            protected_mask=protected_mask,
            page_shape=bgr.shape,
        )
        protected_mask = cv2.bitwise_or(protected_mask, illustration_mask)
    else:
        illustration_mask = np.zeros_like(protected_mask)

    if aggressive_white:
        # Chế độ mạnh:
        # Nền trắng toàn bộ, chỉ copy lại vùng được bảo vệ.
        # Dễ tăng OCR nhưng có thể mất chi tiết ảnh nhạt.
        keep_original_mask = protected_mask
        replace_white_mask = cv2.bitwise_not(keep_original_mask)
    else:
        # Chế độ an toàn:
        # Chỉ xóa pixel được nhận diện là nền sáng/nhạt bởi Otsu,
        # đồng thời không xóa vùng đã được bảo vệ.
        not_protected = cv2.bitwise_not(protected_mask)
        replace_white_mask = cv2.bitwise_and(light_noise_mask, not_protected)
        if np.count_nonzero(watermark_remove_mask) > 0:
            replace_white_mask = cv2.bitwise_or(replace_white_mask, watermark_remove_mask)
            replace_white_mask = cv2.bitwise_and(replace_white_mask, not_protected)
        keep_original_mask = cv2.bitwise_not(replace_white_mask)

    white = np.full_like(bgr, 255)

    original_part = cv2.bitwise_and(bgr, bgr, mask=keep_original_mask)
    white_part = cv2.bitwise_and(white, white, mask=replace_white_mask)

    processed_bgr = cv2.bitwise_or(original_part, white_part)
    processed_rgb = cv2.cvtColor(processed_bgr, cv2.COLOR_BGR2RGB)

    debug_masks = {
        "saturated_mask": saturated_mask,
        "adaptive_dark_mask_otsu_v": dark_mask,
        "adaptive_light_value_mask_otsu_v": light_value_mask,
        "adaptive_light_noise_mask": light_noise_mask,
        "graph_preserve_mask": graph_mask,
        "ly_watermark_remove_mask": watermark_remove_mask,
        "protected_mask": protected_mask,
        "illustration_mask": illustration_mask,
        "replace_white_mask": replace_white_mask,
    }

    meta = {
        "otsu_v_threshold": otsu_threshold,
        "graph_preserve_pixels": int(np.count_nonzero(graph_mask)),
        "ly_watermark_remove_pixels": int(np.count_nonzero(watermark_remove_mask)),
    }

    return processed_rgb, debug_masks, meta



def apply_fixed_border_whiteout(
    image_rgb: np.ndarray,
    header_height: str = DEFAULT_HEADER_HEIGHT,
    footer_height: str = DEFAULT_FOOTER_HEIGHT,
) -> tuple[np.ndarray, int, int]:
    """
    Xóa trắng lề trên/dưới theo khối cố định do người dùng nhập từ GUI.

    Quy tắc:
    - Không dùng cơ chế dò biên tự động.
    - Không tự dò biên nội dung.
    - Header: từ dòng 0 đến dòng int(height * header_ratio).
    - Footer: từ dòng int(height * (1.0 - footer_ratio)) đến hết ảnh.
    - Toàn bộ thân trang nằm giữa hai khối lề được giữ nguyên trong bước này.
    """
    if image_rgb.ndim not in (2, 3):
        raise ValueError("Ảnh đầu vào cho fixed border white-out không hợp lệ.")

    result = image_rgb.copy()
    height = result.shape[0]

    header_px = parse_edge_height(header_height, height)
    footer_px = parse_edge_height(footer_height, height)

    header_end = max(0, min(header_px, height))
    footer_start = max(0, min(height - footer_px, height))

    if result.ndim == 2:
        if header_end > 0:
            result[0:header_end, :] = 255
        if footer_start < height:
            result[footer_start:height, :] = 255
    else:
        if header_end > 0:
            result[0:header_end, :, :] = (255, 255, 255)
        if footer_start < height:
            result[footer_start:height, :, :] = (255, 255, 255)

    return result, header_end, height - footer_start


def process_pdf_page_worker(task: PageTask, process_log_queue: Any = None) -> PageResult:
    """
    Worker xử lý một trang PDF.

    Hàm này chạy trong process riêng:
    - Render đúng một trang PDF.
    - Chạy tiền xử lý ảnh.
    - White-out header/footer.
    - Save PNG đầu ra.
    - Tự bắt exception và trả về PageResult lỗi để app không treo.
    """
    try:
        # Tránh OpenCV tự tạo thêm nhiều thread bên trong mỗi process.
        try:
            cv2.setNumThreads(1)
        except Exception:
            pass

        put_process_log(
            process_log_queue,
            f"  Trang {task.page_index}/{task.total_pages}: render + xử lý...",
        )

        pages = convert_from_path(
            task.pdf_path,
            dpi=task.dpi,
            fmt="ppm",
            first_page=task.page_index,
            last_page=task.page_index,
            thread_count=1,
            poppler_path=task.poppler_path or None,
        )

        if not pages:
            raise RuntimeError(f"Không render được trang {task.page_index}.")

        pil_page = pages[0]
        page_rgb = np.array(pil_page.convert("RGB"))

        settings = task.settings

        processed_rgb, masks, page_meta = process_page_rgb(
            page_rgb=page_rgb,
            keep_sat_lower=hsv_tuple_to_array(settings.keep_sat_lower),
            keep_sat_upper=hsv_tuple_to_array(settings.keep_sat_upper),
            preserve_illustrations=settings.preserve_illustrations,
            aggressive_white=settings.aggressive_white,
            background_saturation_upper=settings.background_saturation_upper,
            graph_preserve_enabled=settings.graph_preserve_enabled,
            graph_faint_gray_max=settings.graph_faint_gray_max,
            graph_neutral_sat_max=settings.graph_neutral_sat_max,
            graph_local_contrast_min=settings.graph_local_contrast_min,
            graph_canny_low=settings.graph_canny_low,
            graph_canny_high=settings.graph_canny_high,
            graph_line_min_len=settings.graph_line_min_len,
            graph_dashed_close_px=settings.graph_dashed_close_px,
            graph_near_line_dilate_px=settings.graph_near_line_dilate_px,
            graph_protect_dilate_px=settings.graph_protect_dilate_px,
            graph_pale_color_sat_min=settings.graph_pale_color_sat_min,
            graph_pale_color_spread_min=settings.graph_pale_color_spread_min,
            ly_watermark_cleanup_enabled=settings.ly_watermark_cleanup_enabled,
            ly_watermark_gray_min=settings.ly_watermark_gray_min,
            ly_watermark_gray_max=settings.ly_watermark_gray_max,
            ly_watermark_sat_max=settings.ly_watermark_sat_max,
            ly_watermark_spread_max=settings.ly_watermark_spread_max,
            ly_watermark_dark_guard_gray=settings.ly_watermark_dark_guard_gray,
            ly_watermark_dilate_px=settings.ly_watermark_dilate_px,
        )

        processed_rgb, header_px, footer_px = apply_fixed_border_whiteout(
            processed_rgb,
            header_height=settings.header_height,
            footer_height=settings.footer_height,
        )
        edge_meta = {"mode": "fixed-border-block"}
        whiteout_mode = "fixed-border-block"

        output_png_path = Path(task.output_png_path)

        Image.fromarray(processed_rgb).save(
            output_png_path,
            format="PNG",
            dpi=(task.dpi, task.dpi),
            optimize=False,
        )

        if task.debug_dir is not None:
            debug_dir = Path(task.debug_dir)
            debug_dir.mkdir(parents=True, exist_ok=True)

            for name, mask in masks.items():
                cv2.imwrite(
                    str(debug_dir / f"page_{task.page_index:04d}_{name}.png"),
                    mask,
                )

            final_preview_bgr = cv2.cvtColor(processed_rgb, cv2.COLOR_RGB2BGR)
            cv2.imwrite(
                str(debug_dir / f"page_{task.page_index:04d}_FINAL_FIXED_BORDER_WHITEOUT.png"),
                final_preview_bgr,
            )

            meta_path = debug_dir / f"page_{task.page_index:04d}_meta.txt"
            meta_lines = [
                f"page_index={task.page_index}",
                f"otsu_v_threshold={page_meta.get('otsu_v_threshold')}",
                f"border_mode={whiteout_mode}",
                f"header_px={header_px}",
                f"footer_px={footer_px}",
            ]
            for key, value in edge_meta.items():
                meta_lines.append(f"{key}={value}")
            meta_path.write_text("\n".join(meta_lines), encoding="utf-8")

        put_process_log(
            process_log_queue,
            (
                f"    Trang {task.page_index}: Otsu V={page_meta.get('otsu_v_threshold')}, "
                f"white-out={whiteout_mode}, header={header_px}px, footer={footer_px}px"
            ),
        )

        return PageResult(
            page_index=task.page_index,
            success=True,
            output_png_path=str(output_png_path),
            header_px=header_px,
            footer_px=footer_px,
            otsu_v_threshold=page_meta.get("otsu_v_threshold"),
            whiteout_mode=whiteout_mode,
        )

    except Exception:
        error_text = traceback.format_exc()
        put_process_log(
            process_log_queue,
            f"    Lỗi trang {task.page_index}: {error_text}",
        )

        return PageResult(
            page_index=task.page_index,
            success=False,
            output_png_path=None,
            error=error_text,
        )


def process_pdf(
    pdf_path: Path,
    output_pdf_path: Path,
    *,
    dpi: int,
    processes: int,
    poppler_path: str | None,
    keep_sat_lower: np.ndarray,
    keep_sat_upper: np.ndarray,
    preserve_illustrations: bool,
    aggressive_white: bool,
    debug_masks: bool,
    header_height: str,
    footer_height: str,
    background_saturation_upper: int,
    log: Callable[[str], None],
    graph_preserve_enabled: bool = DEFAULT_GRAPH_PRESERVE_ENABLED,
    graph_faint_gray_max: int = DEFAULT_GRAPH_FAINT_GRAY_MAX,
    graph_neutral_sat_max: int = DEFAULT_GRAPH_NEUTRAL_SAT_MAX,
    graph_local_contrast_min: int = DEFAULT_GRAPH_LOCAL_CONTRAST_MIN,
    graph_canny_low: int = DEFAULT_GRAPH_CANNY_LOW,
    graph_canny_high: int = DEFAULT_GRAPH_CANNY_HIGH,
    graph_line_min_len: int = DEFAULT_GRAPH_LINE_MIN_LEN,
    graph_dashed_close_px: int = DEFAULT_GRAPH_DASHED_CLOSE_PX,
    graph_near_line_dilate_px: int = DEFAULT_GRAPH_NEAR_LINE_DILATE_PX,
    graph_protect_dilate_px: int = DEFAULT_GRAPH_PROTECT_DILATE_PX,
    graph_pale_color_sat_min: int = DEFAULT_GRAPH_PALE_COLOR_SAT_MIN,
    graph_pale_color_spread_min: int = DEFAULT_GRAPH_PALE_COLOR_SPREAD_MIN,
    ly_watermark_cleanup_enabled: bool = DEFAULT_LY_WATERMARK_CLEANUP_ENABLED,
    ly_watermark_gray_min: int = DEFAULT_LY_WATERMARK_GRAY_MIN,
    ly_watermark_gray_max: int = DEFAULT_LY_WATERMARK_GRAY_MAX,
    ly_watermark_sat_max: int = DEFAULT_LY_WATERMARK_SAT_MAX,
    ly_watermark_spread_max: int = DEFAULT_LY_WATERMARK_SPREAD_MAX,
    ly_watermark_dark_guard_gray: int = DEFAULT_LY_WATERMARK_DARK_GUARD_GRAY,
    ly_watermark_dilate_px: int = DEFAULT_LY_WATERMARK_DILATE_PX,
) -> None:
    """
    Xử lý toàn bộ PDF bằng đa tiến trình.

    Luồng mới:
    - Lấy tổng số trang.
    - Mỗi trang là một PageTask độc lập.
    - ProcessPoolExecutor xử lý song song render + tiền xử lý + white-out.
    - Log từ process con được chuyển về multiprocessing-safe queue,
      sau đó đẩy vào queue.Queue của Tkinter ở main GUI.
    """
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    info = pdfinfo_from_path(
        str(pdf_path),
        poppler_path=poppler_path or None,
    )

    total_pages = int(info["Pages"])
    if total_pages < 1:
        raise RuntimeError("PDF không có trang nào để xử lý.")

    cpu_count = os.cpu_count() or 1
    max_workers = max(1, min(int(processes), cpu_count, total_pages))

    log(f"Tổng số trang: {total_pages}")
    log(f"Số process CPU dùng thực tế: {max_workers}/{cpu_count}")
    log("Bộ lọc nền: Adaptive Otsu trên kênh V từng trang")
    log(f"Header/Footer: Fixed Border Block {header_height}/{footer_height}")

    with tempfile.TemporaryDirectory(prefix="ocr_gui_pages_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        processed_page_paths: list[str | None] = [None] * total_pages

        debug_dir = None
        if debug_masks:
            debug_dir = output_pdf_path.parent / f"{output_pdf_path.stem}_debug_masks"
            debug_dir.mkdir(parents=True, exist_ok=True)

        settings = ProcessingSettings(
            keep_sat_lower=tuple(int(x) for x in keep_sat_lower.tolist()),
            keep_sat_upper=tuple(int(x) for x in keep_sat_upper.tolist()),
            preserve_illustrations=bool(preserve_illustrations),
            aggressive_white=bool(aggressive_white),
            header_height=header_height or DEFAULT_HEADER_HEIGHT,
            footer_height=footer_height or DEFAULT_FOOTER_HEIGHT,
            background_saturation_upper=int(background_saturation_upper),
            graph_preserve_enabled=bool(graph_preserve_enabled),
            graph_faint_gray_max=int(graph_faint_gray_max),
            graph_neutral_sat_max=int(graph_neutral_sat_max),
            graph_local_contrast_min=int(graph_local_contrast_min),
            graph_canny_low=int(graph_canny_low),
            graph_canny_high=int(graph_canny_high),
            graph_line_min_len=int(graph_line_min_len),
            graph_dashed_close_px=int(graph_dashed_close_px),
            graph_near_line_dilate_px=int(graph_near_line_dilate_px),
            graph_protect_dilate_px=int(graph_protect_dilate_px),
            graph_pale_color_sat_min=int(graph_pale_color_sat_min),
            graph_pale_color_spread_min=int(graph_pale_color_spread_min),
            ly_watermark_cleanup_enabled=bool(ly_watermark_cleanup_enabled),
            ly_watermark_gray_min=int(ly_watermark_gray_min),
            ly_watermark_gray_max=int(ly_watermark_gray_max),
            ly_watermark_sat_max=int(ly_watermark_sat_max),
            ly_watermark_spread_max=int(ly_watermark_spread_max),
            ly_watermark_dark_guard_gray=int(ly_watermark_dark_guard_gray),
            ly_watermark_dilate_px=int(ly_watermark_dilate_px),
        )

        tasks: list[PageTask] = []
        for page_index in range(1, total_pages + 1):
            page_path = tmpdir_path / f"page_{page_index:04d}.png"
            tasks.append(
                PageTask(
                    pdf_path=str(pdf_path),
                    output_png_path=str(page_path),
                    page_index=page_index,
                    total_pages=total_pages,
                    dpi=dpi,
                    poppler_path=poppler_path,
                    debug_dir=str(debug_dir) if debug_dir is not None else None,
                    settings=settings,
                )
            )

        # spawn an toàn hơn cho Windows/Tkinter. Nếu lỗi môi trường đặc biệt,
        # fallback về context mặc định.
        try:
            mp_context = mp.get_context("spawn")
        except ValueError:
            mp_context = mp.get_context()

        errors: list[str] = []

        with mp_context.Manager() as manager:
            process_log_queue = manager.Queue()

            with concurrent.futures.ProcessPoolExecutor(
                max_workers=max_workers,
                mp_context=mp_context,
            ) as executor:
                future_to_page = {
                    executor.submit(
                        process_pdf_page_worker,
                        task,
                        process_log_queue,
                    ): task.page_index
                    for task in tasks
                }

                completed = 0

                while future_to_page:
                    done, _ = concurrent.futures.wait(
                        future_to_page,
                        timeout=0.15,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )

                    drain_process_log_queue(process_log_queue, log)

                    if not done:
                        continue

                    for future in done:
                        page_index = future_to_page.pop(future)

                        try:
                            result = future.result()
                        except Exception:
                            error_text = (
                                f"Lỗi không bắt được ở trang {page_index}:\n"
                                + traceback.format_exc()
                            )
                            errors.append(error_text)
                            log(error_text)
                            continue

                        if not result.success:
                            errors.append(
                                result.error or f"Trang {page_index} xử lý thất bại."
                            )
                            continue

                        if not result.output_png_path:
                            errors.append(f"Trang {page_index} không có file PNG đầu ra.")
                            continue

                        processed_page_paths[page_index - 1] = result.output_png_path
                        completed += 1
                        log(f"  Hoàn tất {completed}/{total_pages} trang.")

                drain_process_log_queue(process_log_queue, log)

        if errors:
            error_report = "\n\n".join(errors[:5])
            if len(errors) > 5:
                error_report += f"\n\n... và {len(errors) - 5} lỗi khác."
            raise RuntimeError(error_report)

        missing_pages = [
            index + 1
            for index, path in enumerate(processed_page_paths)
            if not path or not Path(path).exists()
        ]
        if missing_pages:
            raise RuntimeError(f"Thiếu file ảnh của các trang: {missing_pages}")

        final_page_paths = [str(path) for path in processed_page_paths if path is not None]

        if img2pdf is None:
            raise RuntimeError("Thiếu thư viện img2pdf để chạy process_pdf legacy. main.py không cần nhánh này.")
        layout_fun = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))

        with open(output_pdf_path, "wb") as f:
            f.write(
                img2pdf.convert(
                    final_page_paths,
                    layout_fun=layout_fun,
                )
            )


def process_pdf_unicode_safe(
    pdf_path: Path,
    output_pdf_path: Path,
    **kwargs: Any,
) -> None:
    """
    Chạy process_pdf qua một bản copy có đường dẫn ASCII ngắn.

    Lý do: Poppler/pdfinfo trên Windows đôi khi không mở được file PDF
    khi đường dẫn/tên file có tiếng Việt có dấu, ký tự Unicode hoặc quá dài.
    Python vẫn đọc được đường dẫn gốc, nhưng Poppler có thể báo:
    "Unable to get page count" / "Couldn't open file ... No error".
    """
    log = kwargs.get("log")
    with tempfile.TemporaryDirectory(prefix="ocr_safe_src_") as tmpdir:
        safe_pdf_path = Path(tmpdir) / "input.pdf"
        shutil.copy2(pdf_path, safe_pdf_path)
        if callable(log):
            log(f"Đã copy nguồn sang đường dẫn tạm ASCII để Poppler đọc ổn định: {safe_pdf_path}")
        process_pdf(
            pdf_path=safe_pdf_path,
            output_pdf_path=output_pdf_path,
            **kwargs,
        )

# ---------------------------------------------------------------------------
# GUI removed in unified-tool build V7.7.7.
#
# This file is the uploaded IPCLASS/Lý processing engine with only the old
# standalone Tkinter interface removed.  The unified application loads the
# functions above from main.py via LyProcessor, so run: python main.py
# ---------------------------------------------------------------------------

