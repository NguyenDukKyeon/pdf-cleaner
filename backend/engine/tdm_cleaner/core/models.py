from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


@dataclass(slots=True)
class PagePatch:
    """A local raster patch encoded as PNG, mapped back onto the original PDF page."""

    x_px: int
    y_px: int
    w_px: int
    h_px: int
    image_bytes: bytes
    has_alpha: bool = True


@dataclass(slots=True)
class VectorLineSegment:
    """Optional vector overlay for reconstructed ruled lines."""

    x0_px: int
    y0_px: int
    x1_px: int
    y1_px: int
    width_px: float
    rgb: tuple[float, float, float]
    opacity: float = 0.35
    dashed: bool = False


@dataclass(slots=True)
class PageResult:
    """Output of one processed PDF page.

    mode="full_raster" keeps the V7.6 raster behavior: the whole page is stored as
    image_bytes. mode="hybrid" preserves the source PDF page and overlays only
    local patches/vector lines.
    """

    page_index: int
    width_pt: float
    height_pt: float
    image_bytes: bytes | None = None
    error: str | None = None
    used_original_fallback: bool = False
    mode: Literal["full_raster", "hybrid"] = "full_raster"
    img_w: int = 0
    img_h: int = 0
    patches: list[PagePatch] = field(default_factory=list)
    vector_lines: list[VectorLineSegment] = field(default_factory=list)
    patch_coverage: float = 0.0
    changed_pixel_ratio: float = 0.0
