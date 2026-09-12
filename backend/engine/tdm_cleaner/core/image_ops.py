from __future__ import annotations

from typing import Any
from functools import lru_cache
import cv2
import numpy as np


def clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except Exception:
        v = int(default)
    return max(lo, min(hi, v))


def effective_render_dpi(render_dpi: int, output_dpi: int) -> int:
    """Avoid rendering more pixels than the output PDF will keep."""
    render_dpi = clamp_int(render_dpi, 250, 72, 600)
    output_dpi = clamp_int(output_dpi, render_dpi, 72, 600)
    return min(render_dpi, output_dpi)




@lru_cache(maxsize=64)
def get_structuring_element_cached(shape: int, ksize: tuple[int, int]) -> np.ndarray:
    """Cache small OpenCV morphology kernels per process.

    This is a runtime-only optimization: the kernel values are exactly the same
    as cv2.getStructuringElement(shape, ksize), but repeated pages do not pay
    allocation overhead again.  Each worker process owns its own cache, so there
    is no cross-process shared mutable state and no race condition.
    """
    return cv2.getStructuringElement(int(shape), (int(ksize[0]), int(ksize[1])))


def parse_edge_height(value: Any, image_height: int) -> int:
    value = str(value or "").strip()
    if not value:
        return 0
    try:
        if value.endswith("%"):
            number = float(value[:-1].strip())
            pixels = int(image_height * number / 100.0)
        else:
            number = float(value)
            pixels = int(image_height * number) if 0 < number < 1 else int(number)
    except Exception:
        return 0
    return max(0, min(pixels, image_height))


def apply_fixed_border_whiteout(image_rgb: np.ndarray, header_height: Any, footer_height: Any) -> np.ndarray:
    """White-out fixed header/footer strips after watermark processing.

    V7.7.4 performance: parse first and copy only when there is a real
    non-zero strip to modify.  Values such as "0%" are common in TDM/Toán
    mode and should not trigger a full-page image copy.
    """
    h = image_rgb.shape[0]
    header_px = parse_edge_height(header_height, h)
    footer_px = parse_edge_height(footer_height, h)
    if header_px <= 0 and footer_px <= 0:
        return image_rgb
    out = image_rgb.copy()
    if header_px > 0:
        out[:header_px, :, :] = 255
    if footer_px > 0:
        out[h - footer_px :, :, :] = 255
    return out


def encode_rgb_to_jpeg_bytes(image_rgb: np.ndarray, *, output_grayscale: bool, quality: int) -> bytes:
    """Fast JPEG encoding using OpenCV; preserves color text with 4:4:4 when available."""
    image_rgb = np.clip(image_rgb, 0, 255).astype(np.uint8, copy=False)
    quality = clamp_int(quality, 90, 50, 100)

    if output_grayscale:
        enc_src = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    else:
        enc_src = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        if hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR") and hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444"):
            params += [int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR), int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444)]

    ok, encoded = cv2.imencode(".jpg", enc_src, params)
    if not ok:
        raise RuntimeError("Không encode được ảnh trang sang JPEG.")
    return encoded.tobytes()


def encode_rgb_to_png_bytes(image_rgb: np.ndarray, *, output_grayscale: bool = False, png_compression: int = 3) -> bytes:
    """Lossless PNG encoding.

    When output_grayscale=True, encode a real single-channel grayscale PNG instead
    of an RGB PNG with equal-looking channels.  This matters for the "Xuất
    grayscale" option because PyMuPDF embeds the image stream exactly as encoded.
    """
    image_rgb = np.clip(image_rgb, 0, 255).astype(np.uint8, copy=False)
    if output_grayscale:
        enc_src = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    else:
        enc_src = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    png_compression = clamp_int(png_compression, 3, 0, 9)
    ok, encoded = cv2.imencode('.png', enc_src, [int(cv2.IMWRITE_PNG_COMPRESSION), int(png_compression)])
    if not ok:
        raise RuntimeError('Không encode được ảnh sang PNG.')
    return encoded.tobytes()


def encode_rgba_to_png_bytes(image_rgba: np.ndarray, *, png_compression: int = 3) -> bytes:
    """Lossless RGBA PNG encoding; alpha lets hybrid patches avoid hard white boxes."""
    image_rgba = np.clip(image_rgba, 0, 255).astype(np.uint8, copy=False)
    bgra = cv2.cvtColor(image_rgba, cv2.COLOR_RGBA2BGRA)
    png_compression = clamp_int(png_compression, 3, 0, 9)
    ok, encoded = cv2.imencode('.png', bgra, [int(cv2.IMWRITE_PNG_COMPRESSION), int(png_compression)])
    if not ok:
        raise RuntimeError('Không encode được ảnh RGBA sang PNG.')
    return encoded.tobytes()


def encode_rgb_auto_bytes(
    image_rgb: np.ndarray,
    *,
    fmt: str,
    output_grayscale: bool,
    quality: int,
    png_compression: int = 3,
) -> bytes:
    fmt = str(fmt or 'jpg').strip().lower()
    if fmt in {'png', 'lossless'}:
        return encode_rgb_to_png_bytes(image_rgb, output_grayscale=output_grayscale, png_compression=png_compression)
    return encode_rgb_to_jpeg_bytes(image_rgb, output_grayscale=output_grayscale, quality=quality)
