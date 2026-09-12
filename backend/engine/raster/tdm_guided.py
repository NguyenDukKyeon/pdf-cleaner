from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping

import cv2
import fitz
import numpy as np
from PIL import Image

from .image_extractor import extract_native_page_image

TAILIEUONTHI_MARKER = "tailieuonthi"
DEFAULT_WORK_DPI = 200
MAX_WATERMARK_RESIDUAL_SCORE = 0.08


@dataclass(frozen=True, slots=True)
class TdmGuidedResult:
    changed_pages: int
    changed_pixels: int
    native_image_pages: int
    watermark_residual_score: float
    outside_change_ratio: float
    work_dpi: int
    changed_pixel_ratio: float = 0.0
    page_residual_scores: tuple[float, ...] = ()


def transfer_working_cleanup_to_native(
    native_rgb: np.ndarray,
    cleaned_working_rgb: np.ndarray,
    trusted_mask: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Transfer only trusted cleanup pixels back to the native raster image.

    The legacy TDM algorithm operates on a bounded working image.  Everything
    outside its explicit header/footer/diagonal cleanup mask is copied byte-for-
    byte from the native XObject so V2 never turns a successful watermark repair
    into a whole-page resample.
    """

    native = np.asarray(native_rgb)
    cleaned = np.asarray(cleaned_working_rgb)
    mask = np.asarray(trusted_mask)
    if native.ndim != 3 or native.shape[2] < 3:
        raise ValueError("native_rgb must be an RGB image")
    if cleaned.ndim != 3 or cleaned.shape[2] < 3:
        raise ValueError("cleaned_working_rgb must be an RGB image")
    if mask.ndim != 2 or mask.shape != cleaned.shape[:2]:
        raise ValueError("trusted_mask must match working image geometry")

    height, width = native.shape[:2]
    upscaled = cv2.resize(cleaned[..., :3], (width, height), interpolation=cv2.INTER_CUBIC)
    native_mask = cv2.resize(mask.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST) > 0

    output = native[..., :3].astype(np.uint8, copy=True)
    output[native_mask] = upscaled[native_mask]
    changed = int(np.count_nonzero(np.any(output != native[..., :3], axis=2)))
    return output, changed


def compute_watermark_residual_score(
    original_rgb: np.ndarray,
    cleaned_rgb: np.ndarray,
    candidate_mask: np.ndarray,
    background_gray: np.ndarray,
) -> float:
    """Return remaining watermark contrast as a fraction of original contrast.

    0 means the candidate pixels were restored to their estimated paper
    background; 1 means the watermark contrast is effectively unchanged.
    Only pixels that the reference cleanup actually changed are scored, which
    excludes protected text/lines intentionally left intact by TDM V7.
    """

    original = np.asarray(original_rgb)[..., :3].astype(np.uint8, copy=False)
    cleaned = np.asarray(cleaned_rgb)[..., :3].astype(np.uint8, copy=False)
    candidate = np.asarray(candidate_mask, dtype=bool)
    bg = np.asarray(background_gray)
    if original.shape != cleaned.shape:
        raise ValueError("original and cleaned images must have matching geometry")
    if candidate.shape != original.shape[:2] or bg.shape != candidate.shape:
        raise ValueError("candidate/background geometry must match image")

    diff = np.max(np.abs(cleaned.astype(np.int16) - original.astype(np.int16)), axis=2)
    acted = candidate & (diff >= 3)
    if not np.any(acted):
        return 1.0 if np.any(candidate) else 0.0

    original_gray = cv2.cvtColor(original, cv2.COLOR_RGB2GRAY).astype(np.int16)
    cleaned_gray = cv2.cvtColor(cleaned, cv2.COLOR_RGB2GRAY).astype(np.int16)
    bg_i = bg.astype(np.int16, copy=False)
    original_contrast = np.clip(bg_i - original_gray, 0, 255)
    residual_contrast = np.clip(bg_i - cleaned_gray, 0, 255)
    denominator = float(np.sum(original_contrast[acted]))
    if denominator <= 0.0:
        return 0.0
    score = float(np.sum(residual_contrast[acted]) / denominator)
    return float(np.clip(score, 0.0, 1.0))


def _load_tdm_module():
    module_path = Path(__file__).resolve().parents[1] / "watermaker TDM.py"
    module_name = "pdf_cleaner_tdm_guided_legacy"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TDM watermark engine")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_tdm_tuning() -> Mapping[str, object]:
    config_path = Path(__file__).resolve().parents[1] / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    # The mature TDM V7 tuning currently lives in the historical `toan` config
    # block.  It is used here as watermark-signature tuning only; the V2 router,
    # not the subject profile, decides whether this adapter runs.
    return dict(data.get("modes", {}).get("toan", {}))


def _working_geometry(page_rect: fitz.Rect, native_shape: tuple[int, int], work_dpi: int) -> tuple[int, int]:
    native_h, native_w = native_shape
    target_w = max(1, int(round(float(page_rect.width) * int(work_dpi) / 72.0)))
    target_h = max(1, int(round(float(page_rect.height) * int(work_dpi) / 72.0)))
    return min(native_w, target_w), min(native_h, target_h)


def _debug_mask(debug: Mapping[str, np.ndarray], name: str, shape: tuple[int, int]) -> np.ndarray:
    value = debug.get(name)
    if value is None:
        return np.zeros(shape, dtype=bool)
    arr = np.asarray(value)
    if arr.shape != shape:
        arr = cv2.resize(arr.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return arr > 0


def _clean_native_page(input_pdf: Path, page_index: int, output_png: Path, *, work_dpi: int) -> dict[str, object]:
    module = _load_tdm_module()
    settings = module.create_settings_from_config(_load_tdm_tuning())
    args = module._namespace_from_settings(settings)
    args._need_debug = True

    with fitz.open(str(input_pdf)) as doc:
        page = doc[page_index]
        native = extract_native_page_image(doc, page_index)
        if native is None:
            raise RuntimeError(f"page {page_index + 1} is not a conservative full-page native image")
        with Image.open(io.BytesIO(native.image_bytes)) as image:
            native_rgb = np.array(image.convert("RGB"))
        work_w, work_h = _working_geometry(page.rect, native_rgb.shape[:2], work_dpi)

    working = cv2.resize(native_rgb, (work_w, work_h), interpolation=cv2.INTER_AREA)
    cleaned_bgr, debug = module.clean_page_bgr(cv2.cvtColor(working, cv2.COLOR_RGB2BGR), args)
    cleaned = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)

    shape = working.shape[:2]
    header_footer = _debug_mask(debug, "header_footer_mask", shape)
    diagonal_band = _debug_mask(debug, "diag_band", shape)
    diagonal_replace = _debug_mask(debug, "diag_replace", shape)
    changed_working = np.max(np.abs(cleaned.astype(np.int16) - working.astype(np.int16)), axis=2) >= 2
    trusted = changed_working & (header_footer | diagonal_band)

    candidate = header_footer | diagonal_replace
    background = debug.get("local_bg_gray")
    if background is None:
        gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
        background = cv2.GaussianBlur(gray, (61, 61), 0)
    residual = compute_watermark_residual_score(working, cleaned, candidate, np.asarray(background))

    output_rgb, changed_pixels = transfer_working_cleanup_to_native(native_rgb, cleaned, trusted)
    native_mask = cv2.resize(trusted.astype(np.uint8), (native_rgb.shape[1], native_rgb.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    changed_map = np.any(output_rgb != native_rgb, axis=2)
    outside = ~native_mask
    outside_ratio = float(np.count_nonzero(changed_map & outside) / max(1, np.count_nonzero(outside)))
    changed_ratio = float(np.count_nonzero(changed_map) / max(1, changed_map.size))

    output_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(output_rgb).save(output_png, format="PNG", compress_level=6)
    return {
        "changed_pixels": changed_pixels,
        "changed_pixel_ratio": changed_ratio,
        "outside_change_ratio": outside_ratio,
        "watermark_residual_score": residual,
        "width": int(output_rgb.shape[1]),
        "height": int(output_rgb.shape[0]),
        "work_dpi": int(work_dpi),
    }


def _run_page_worker(
    input_pdf: Path,
    page_index: int,
    output_png: Path,
    result_json: Path,
    *,
    work_dpi: int,
    should_cancel: Callable[[], bool] | None,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + current_pythonpath if current_pythonpath else "")
    cmd = [
        sys.executable,
        "-m",
        "backend.engine.raster.tdm_guided",
        "--worker",
        "--input-pdf",
        str(input_pdf),
        "--page-index",
        str(page_index),
        "--output-png",
        str(output_png),
        "--result-json",
        str(result_json),
        "--work-dpi",
        str(work_dpi),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        while proc.poll() is None:
            if should_cancel and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("cancelled")
            time.sleep(0.1)
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        if proc.returncode != 0:
            raise RuntimeError(f"TDM guided page {page_index + 1} failed: {stderr.strip()[-1200:]}")
        if not result_json.exists() or not output_png.exists():
            raise RuntimeError(f"TDM guided page {page_index + 1} produced incomplete output")
        return json.loads(result_json.read_text(encoding="utf-8"))
    finally:
        if proc.stderr is not None:
            proc.stderr.close()


def _validate_safe_raster_document(input_pdf: Path) -> tuple[list[tuple[float, float]], dict[str, str]]:
    geometries: list[tuple[float, float]] = []
    with fitz.open(str(input_pdf)) as doc:
        metadata = dict(doc.metadata or {})
        for index in range(doc.page_count):
            page = doc[index]
            if int(page.rotation) != 0:
                raise RuntimeError("TDM guided native rebuild does not accept rotated pages")
            if page.first_annot is not None:
                raise RuntimeError("TDM guided native rebuild does not accept annotations")
            unrelated_links = []
            for link in page.get_links():
                uri = str(link.get("uri") or "").casefold()
                known_watermark_link = (
                    int(link.get("kind") or 0) == fitz.LINK_URI
                    and ("tailieuonthi" in uri or "tlot.cc/tailieuonthigroup" in uri)
                )
                if not known_watermark_link:
                    unrelated_links.append(link)
            if unrelated_links:
                raise RuntimeError("TDM guided native rebuild does not accept unrelated links")
            if page.get_text("text").strip():
                raise RuntimeError("TDM guided native rebuild requires raster-only pages")
            if extract_native_page_image(doc, index) is None:
                raise RuntimeError(f"page {index + 1} is not a conservative full-page native image")
            geometries.append((float(page.rect.width), float(page.rect.height)))
    return geometries, metadata


def _assemble_native_pdf(page_pngs: list[Path], geometries: list[tuple[float, float]], output_pdf: Path, metadata: Mapping[str, str]) -> None:
    out = fitz.open()
    try:
        for image_path, (width, height) in zip(page_pngs, geometries, strict=True):
            page = out.new_page(width=width, height=height)
            page.insert_image(page.rect, filename=str(image_path), keep_proportion=False)
        safe_metadata = {k: str(v) for k, v in metadata.items() if v is not None}
        if safe_metadata:
            try:
                out.set_metadata(safe_metadata)
            except Exception:
                pass
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(output_pdf), garbage=4, deflate=True)
    finally:
        out.close()


def clean_tailieuonthi_document(
    input_pdf: str | Path,
    output_pdf: str | Path,
    *,
    work_dpi: int = DEFAULT_WORK_DPI,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TdmGuidedResult:
    input_path = Path(input_pdf).expanduser().resolve()
    output_path = Path(output_pdf).expanduser().resolve()
    geometries, metadata = _validate_safe_raster_document(input_path)
    total = len(geometries)
    changed_pixels = 0
    changed_pages = 0
    native_pages = 0
    total_native_pixels = 0
    outside_weighted = 0.0
    residual_scores: list[float] = []

    with tempfile.TemporaryDirectory(prefix="pdfcleaner_tdm_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        page_pngs: list[Path] = []
        for index in range(total):
            if should_cancel and should_cancel():
                raise RuntimeError("cancelled")
            page_png = temp_dir / f"page-{index:05d}.png"
            result_json = temp_dir / f"page-{index:05d}.json"
            metrics = _run_page_worker(
                input_path,
                index,
                page_png,
                result_json,
                work_dpi=int(work_dpi),
                should_cancel=should_cancel,
            )
            page_pngs.append(page_png)
            page_changed = int(metrics.get("changed_pixels", 0) or 0)
            pixels = max(1, int(metrics.get("width", 1)) * int(metrics.get("height", 1)))
            changed_pixels += page_changed
            changed_pages += int(page_changed > 0)
            native_pages += 1
            total_native_pixels += pixels
            outside_weighted += float(metrics.get("outside_change_ratio", 0.0) or 0.0) * pixels
            residual_scores.append(float(metrics.get("watermark_residual_score", 0.0) or 0.0))
            if progress:
                progress(index + 1, total, "TDM-guided native watermark repair")
            if log:
                log(
                    f"TDM-guided page {index + 1}/{total}: residual={residual_scores[-1]:.4f} "
                    f"changed={page_changed}"
                )

        _assemble_native_pdf(page_pngs, geometries, output_path, metadata)

    residual = max(residual_scores, default=0.0)
    outside_ratio = float(outside_weighted / max(1, total_native_pixels))
    changed_ratio = float(changed_pixels / max(1, total_native_pixels))
    if residual > MAX_WATERMARK_RESIDUAL_SCORE:
        raise RuntimeError(
            f"TDM-guided watermark residual {residual:.4f} exceeds {MAX_WATERMARK_RESIDUAL_SCORE:.4f}"
        )
    return TdmGuidedResult(
        changed_pages=changed_pages,
        changed_pixels=changed_pixels,
        native_image_pages=native_pages,
        watermark_residual_score=residual,
        outside_change_ratio=outside_ratio,
        work_dpi=int(work_dpi),
        changed_pixel_ratio=changed_ratio,
        page_residual_scores=tuple(residual_scores),
    )



def probe_tailieuonthi_signature(
    doc: fitz.Document,
    sampled_pages: tuple[int, ...],
    *,
    work_dpi: int = 120,
) -> tuple[float, tuple[int, ...]]:
    """Probe the mature TDM detector on at most two representative raster pages.

    This is a classifier only: it runs the cheap header/footer + diagonal detector
    and performs no output mutation.  A page must contain both independent TDM
    evidence families before it contributes to a high-confidence signature match.
    """

    if not sampled_pages:
        return 0.0, ()
    selected = (sampled_pages[0],) if len(sampled_pages) == 1 else (sampled_pages[0], sampled_pages[-1])
    module = _load_tdm_module()
    settings = module.create_settings_from_config(_load_tdm_tuning())
    matched: list[int] = []
    page_scores: list[float] = []
    for page_index in selected:
        page = doc[int(page_index)]
        native = extract_native_page_image(doc, int(page_index))
        if native is None:
            continue
        with Image.open(io.BytesIO(native.image_bytes)) as image:
            native_rgb = np.array(image.convert("RGB"))
        work_w, work_h = _working_geometry(page.rect, native_rgb.shape[:2], int(work_dpi))
        working = cv2.resize(native_rgb, (work_w, work_h), interpolation=cv2.INTER_AREA)
        args = module._namespace_from_settings(settings)
        args._need_debug = False
        cleaned_hf, hf_mask = module.guided_header_footer_cleanup(
            cv2.cvtColor(working, cv2.COLOR_RGB2BGR), args
        )
        _, diag_debug = module.smart_diagonal_cleanup_v5(cleaned_hf, args)
        pixels = max(1, work_w * work_h)
        hf_ratio = float(np.count_nonzero(hf_mask) / pixels)
        diag_ratio = float(np.count_nonzero(diag_debug.get("diag_replace")) / pixels)
        # Empirical signature bounds from the supplied TaiLieuOnThi family are
        # roughly 0.2-0.3% header/footer and 0.27-0.85% diagonal pixels at 120 DPI.
        # Requiring both independent regions keeps ordinary raster documents out.
        if hf_ratio >= 0.0010 and diag_ratio >= 0.0010:
            matched.append(int(page_index))
            hf_score = min(1.0, hf_ratio / 0.0020)
            diag_score = min(1.0, diag_ratio / 0.0025)
            page_scores.append(0.5 * hf_score + 0.5 * diag_score)

    if not matched:
        return 0.0, ()
    repetition = len(matched) / len(selected)
    evidence_strength = float(sum(page_scores) / len(page_scores))
    confidence = float(np.clip(0.70 + 0.20 * repetition + 0.08 * evidence_strength, 0.0, 0.98))
    if len(selected) > 1 and len(matched) < 2:
        confidence = min(confidence, 0.84)
    return confidence, tuple(matched)

def _worker_main(args: argparse.Namespace) -> int:
    metrics = _clean_native_page(
        Path(args.input_pdf).expanduser().resolve(),
        int(args.page_index),
        Path(args.output_png).expanduser().resolve(),
        work_dpi=int(args.work_dpi),
    )
    result_path = Path(args.result_json).expanduser().resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(metrics, sort_keys=True), encoding="utf-8")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--input-pdf")
    parser.add_argument("--page-index", type=int)
    parser.add_argument("--output-png")
    parser.add_argument("--result-json")
    parser.add_argument("--work-dpi", type=int, default=DEFAULT_WORK_DPI)
    return parser.parse_args(argv)


if __name__ == "__main__":
    ns = _parse_args()
    if not ns.worker:
        raise SystemExit("tdm_guided module is intended to run in --worker mode")
    raise SystemExit(_worker_main(ns))
