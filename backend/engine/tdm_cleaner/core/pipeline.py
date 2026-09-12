from __future__ import annotations

from pathlib import Path
from typing import Any
import concurrent.futures
import gc
import os

import fitz

from .image_ops import clamp_int, effective_render_dpi
from .models import CancelCallback, LogCallback, PageResult, ProgressCallback
from .pdf_io import close_cached_docs, insert_page_result, read_pdf_page_count, save_pdf_atomic
from .worker_pool import auto_worker_count, get_cached_processor, process_page_worker_from_context, process_page_with_processor, worker_init


def process_pdf_optimized(
    *,
    input_pdf: Path,
    output_pdf: Path,
    mode: str,
    mode_config: dict[str, Any],
    dpi: int,
    output_dpi: int,
    quality: int,
    workers: int = 0,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> dict[str, Any]:
    """Hybrid PDF pipeline: render/analyze -> clean -> overlay local patches, with full-raster fallback."""
    input_pdf = Path(input_pdf).expanduser().resolve()
    output_pdf = Path(output_pdf).expanduser().resolve()
    mode_config = dict(mode_config or {})
    mode_config.pop("display_name", None)
    mode_key = str(mode).strip().lower()
    if mode_key in {"toan", "tdm"}:
        mode_config["graph_safe_paper_mode"] = True
    elif mode_key in {"ly", "ipclass"}:
        # IPCLASS is a whole-page OCR preprocessor. It changes many background
        # pixels around text, so hybrid patch overlay can create visible halos
        # by leaving the source PDF under the patch edges. Keep the IPCLASS
        # algorithm exactly the same, but assemble Lý pages as lossless full
        # raster PNG unless the user explicitly chose grayscale.
        mode_config["output_mode"] = "full_raster"
        mode_config.setdefault("full_raster_format", "png")
        mode_config.setdefault("fallback_image_format", "png")
    elif mode_key in {"ebook"}:
        # EBOOK watermark cleanup also works on a whole rendered page. On pages
        # where the diagonal watermark crosses text/table lines, hybrid overlay
        # can leave the original PDF visible below patch edges and make the
        # diagonal band look like dark scratches/halos. Do not change the EBOOK
        # pixel cleanup algorithm; only assemble EBOOK pages as lossless full
        # raster PNG to avoid hybrid-placement artifacts.
        mode_config["output_mode"] = "full_raster"
        mode_config.setdefault("full_raster_format", "png")
        mode_config.setdefault("fallback_image_format", "png")

    should_cancel = should_cancel or (lambda: False)
    log = log or (lambda _s: None)
    progress = progress or (lambda _done, _total, _msg: None)

    output_grayscale = bool(mode_config.get("output_grayscale", True))
    output_mode = str(mode_config.get("output_mode", mode_config.get("pdf_output_mode", "hybrid_auto")))
    output_mode_key = output_mode.strip().lower()
    full_raster_only = output_grayscale or output_mode_key in {"raster", "full_raster", "image", "legacy"}

    src_doc: fitz.Document | None = None
    if full_raster_only:
        page_count = read_pdf_page_count(input_pdf)
    else:
        src_doc = fitz.open(str(input_pdf))
        page_count = int(len(src_doc))
    if page_count <= 0:
        raise RuntimeError("PDF không có trang nào.")

    render_dpi = effective_render_dpi(int(dpi), int(output_dpi))
    quality = clamp_int(quality, 90, 50, 100)
    worker_count = auto_worker_count(int(workers or 0), page_count, render_dpi)

    log(f"Tổng trang: {page_count}; render DPI: {render_dpi}; worker: {worker_count}; quality: {quality}")
    log(f"Pipeline V8.8.9: smart-speed/quality-locked; output_mode={output_mode}")

    out_doc = fitz.open()
    warnings: list[str] = []
    full_raster_pages: set[int] = set()
    fallback_pages: set[int] = set()
    results_buffer: dict[int, PageResult] = {}
    next_to_write = 0
    submitted = 0
    completed = 0
    max_pending = max(1, worker_count * 2)
    tmp_replace_path: Path | None = None
    processing_saved = False
    report: dict[str, Any] | None = None
    same_output_as_input = input_pdf == output_pdf

    if output_grayscale:
        log("Xuất grayscale: bật full-raster để toàn bộ trang PDF là ảnh xám thật.")
    if same_output_as_input:
        log("Ghi đè file gốc: sẽ lưu vào file tạm, đóng PDF gốc, rồi mới thay thế an toàn.")

    def submit_next(executor: concurrent.futures.ProcessPoolExecutor, futures: dict[Any, int]) -> None:
        nonlocal submitted
        while submitted < page_count and len(futures) < max_pending and not should_cancel():
            fut = executor.submit(process_page_worker_from_context, submitted)
            futures[fut] = submitted
            submitted += 1

    try:
        if worker_count <= 1:
            processor = None
            for i in range(page_count):
                if should_cancel():
                    raise RuntimeError("Đã hủy bởi người dùng.")
                if processor is None:
                    processor = get_cached_processor(mode, mode_config)
                result = process_page_with_processor(
                    pdf_path=input_pdf,
                    page_index=i,
                    processor=processor,
                    mode_config=mode_config,
                    dpi=render_dpi,
                    quality=quality,
                    output_grayscale=output_grayscale,
                    use_doc_cache=True,
                )
                insert_page_result(out_doc, src_doc, result)
                if result.mode == "full_raster":
                    full_raster_pages.add(i + 1)
                if result.used_original_fallback:
                    fallback_pages.add(i + 1)
                completed += 1
                if result.error:
                    msg = f"Trang {i + 1}: lỗi xử lý, đã giữ ảnh gốc fallback."
                    warnings.append(msg)
                    log("  " + msg)
                else:
                    log(f"  Trang {i + 1}/{page_count}: OK ({result.mode}; patches={len(result.patches)}; coverage={result.patch_coverage:.3f})")
                progress(completed, page_count, f"Trang {i + 1}/{page_count}")
        else:
            futures: dict[Any, int] = {}
            worker_context = {
                "pdf_path": str(input_pdf),
                "mode": mode,
                "mode_config": mode_config,
                "dpi": render_dpi,
                "quality": quality,
                "output_grayscale": output_grayscale,
            }
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=worker_count,
                initializer=worker_init,
                initargs=(1, worker_context),
            ) as executor:
                submit_next(executor, futures)
                while futures:
                    if should_cancel():
                        for fut in futures:
                            fut.cancel()
                        raise RuntimeError("Đã hủy bởi người dùng.")

                    done, _pending = concurrent.futures.wait(
                        futures,
                        timeout=0.15,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    if not done:
                        continue

                    for fut in done:
                        futures.pop(fut, None)
                        result = fut.result()
                        results_buffer[result.page_index] = result
                        if result.mode == "full_raster":
                            full_raster_pages.add(result.page_index + 1)
                        if result.used_original_fallback:
                            fallback_pages.add(result.page_index + 1)
                        completed += 1
                        if result.error:
                            msg = f"Trang {result.page_index + 1}: lỗi xử lý, đã giữ ảnh gốc fallback."
                            warnings.append(msg)
                            log("  " + msg)
                        else:
                            log(f"  Trang {result.page_index + 1}/{page_count}: OK ({result.mode}; patches={len(result.patches)}; coverage={result.patch_coverage:.3f})")
                        progress(completed, page_count, f"Trang {result.page_index + 1}/{page_count}")

                    while next_to_write in results_buffer:
                        result = results_buffer.pop(next_to_write)
                        insert_page_result(out_doc, src_doc, result)
                        next_to_write += 1
                        del result

                    submit_next(executor, futures)

            if next_to_write != page_count:
                missing = [i + 1 for i in range(page_count) if i >= next_to_write and i not in results_buffer]
                raise RuntimeError(f"Thiếu kết quả trang khi build PDF: {missing[:10]}")

        if same_output_as_input:
            tmp_replace_path = save_pdf_atomic(out_doc, output_pdf, replace=False)
        else:
            save_pdf_atomic(out_doc, output_pdf)

        processing_saved = True
        report = {
            "pages": page_count,
            "workers": worker_count,
            "render_dpi": render_dpi,
            "quality": quality,
            "output_mode": "full_raster_grayscale" if output_grayscale else output_mode,
            "full_raster_pages": sorted(full_raster_pages),
            "fallback_pages": sorted(fallback_pages),
            "warnings": warnings,
        }
    finally:
        out_doc.close()
        try:
            if src_doc is not None:
                src_doc.close()
        except Exception:
            pass
        close_cached_docs()
        results_buffer.clear()
        gc.collect()
        if (not processing_saved) and tmp_replace_path and tmp_replace_path.exists():
            try:
                tmp_replace_path.unlink()
            except Exception:
                pass

    if tmp_replace_path is not None:
        os.replace(str(tmp_replace_path), str(output_pdf))

    if report is None:
        raise RuntimeError("Không tạo được báo cáo xử lý PDF.")
    return report
