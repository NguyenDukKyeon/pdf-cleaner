from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
ENGINE_DIR = BACKEND_DIR / "engine"
APP_DATA_DIR = BACKEND_DIR / "app_data"
OUTPUT_DIR = APP_DATA_DIR / "outputs"
LOG_DIR = APP_DATA_DIR / "logs"
TECH_LOG = LOG_DIR / "ui_processing.log"

for folder in (OUTPUT_DIR, LOG_DIR):
    folder.mkdir(parents=True, exist_ok=True)

# Keep the original engine import style working. The uploaded tool uses root-level
# compatibility modules such as config_manager.py and pdf_pipeline.py.
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from config_manager import ConfigManager, QUALITY_PRESETS  # type: ignore  # noqa: E402
# The processing engine is intentionally imported lazily.  The native desktop
# shell can therefore paint its window immediately; the expensive PDF modules
# are loaded only after the user presses "Xử lý PDF".
PRESETS: dict[str, Any] = {}
clean_pdf_stream_safe: Any = None
process_pdf_optimized: Any = None
run_quality_check: Any = None
ENGINE_IMPORT_LOCK = threading.Lock()


def load_processing_engine() -> None:
    global PRESETS, clean_pdf_stream_safe, process_pdf_optimized, run_quality_check
    if process_pdf_optimized is not None:
        return
    with ENGINE_IMPORT_LOCK:
        if process_pdf_optimized is not None:
            return
        from core_stream import PRESETS as _PRESETS, clean_pdf_stream_safe as _clean_pdf_stream_safe  # type: ignore
        from pdf_pipeline import process_pdf_optimized as _process_pdf_optimized  # type: ignore
        from quality_check_pdf import run_quality_check as _run_quality_check  # type: ignore

        PRESETS = _PRESETS
        clean_pdf_stream_safe = _clean_pdf_stream_safe
        process_pdf_optimized = _process_pdf_optimized
        run_quality_check = _run_quality_check


CONFIG_PATH = ENGINE_DIR / "config.json"
CONFIG_LOCK = threading.Lock()
JOBS_LOCK = threading.Lock()

DOCUMENT_TYPES = {
    "toan": "Toán - TDM",
    "ly": "Lý - IPCLASS",
    "hoa": "Hóa - TYHH",
    "ebook": "Ebook",
}

def _build_preset_aliases() -> dict[str, dict[str, int]]:
    aliases: dict[str, dict[str, int]] = {}
    for name, values in QUALITY_PRESETS.items():
        aliases[str(name)] = {
            "dpi": int(values.get("dpi", 240)),
            "output_dpi": int(values.get("output_pdf_dpi", values.get("dpi", 240))),
            "quality": int(values.get("jpeg_quality", 92)),
            # Desktop-only build intentionally stays in one Python process.
            "cpu": 1,
        }
    return aliases


PRESET_ALIASES = _build_preset_aliases()
MAX_RETAINED_EVENTS = 1000
MAX_JOB_HISTORY = 20


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_tech_log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with TECH_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def config_manager() -> ConfigManager:
    manager = ConfigManager(CONFIG_PATH)
    manager.load()
    return manager


def safe_filename(name: str) -> str:
    base = Path(name or "input.pdf").name
    if not base.lower().endswith(".pdf"):
        base = f"{Path(base).stem}.pdf"
    base = re.sub(r"[^A-Za-z0-9._()\-\u00C0-\u024F\u1E00-\u1EFF ]+", "_", base).strip()
    return base or "input.pdf"


def unique_path(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for i in range(2, 10000):
        candidate = folder / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Không thể tạo tên file duy nhất.")


def jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [jsonable(v) for v in value]
    return value


def make_processing_output_path(output_path: Path) -> Path:
    """Create a deterministic temporary PDF path near the final output.

    This is output-management glue for the HTML app only. The PDF processing
    algorithm itself is loaded from backend/engine copied from the single-result
    TDM build.
    """
    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path.with_name(f"{output_path.stem}.processing_{uuid.uuid4().hex[:8]}.tmp.pdf")


def cleanup_staging_output(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def preflight_pdf(input_pdf: Path, mode: str, mode_config: dict[str, Any]) -> dict[str, Any]:
    """Small metadata preflight used by the HTML wrapper."""
    import fitz

    result: dict[str, Any] = {
        "mode": mode,
        "warnings": [],
        "text_chars": 0,
        "links": 0,
        "annots": 0,
        "full_raster_output": bool(mode_config.get("output_grayscale", False)) or str(mode_config.get("output_mode", "")).lower() in {"raster", "full_raster", "image", "legacy"},
    }
    with fitz.open(str(input_pdf)) as doc:
        result["pages"] = len(doc)
        text_chars = 0
        links = 0
        annots = 0
        for page in doc:
            try:
                text_chars += len(page.get_text("text") or "")
            except Exception:
                pass
            try:
                links += len(page.get_links() or [])
            except Exception:
                pass
            try:
                annots += sum(1 for _ in (page.annots() or []))
            except Exception:
                pass
        result["text_chars"] = int(text_chars)
        result["links"] = int(links)
        result["annots"] = int(annots)
    if result.get("pages", 0) <= 0:
        result.setdefault("warnings", []).append("PDF không có trang nào.")
    return result


def promote_processed_pdf(staging_output: Path, output_path: Path, *, backup_existing: bool = False) -> None:
    staging_output = Path(staging_output).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    if not staging_output.exists():
        raise RuntimeError(f"Không tìm thấy file tạm để xuất: {staging_output}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_existing and output_path.exists():
        backup_dir = output_path.parent / "_backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = unique_path(backup_dir, f"{output_path.stem}_{stamp}{output_path.suffix}")
        shutil.copy2(str(output_path), str(backup_path))
    os.replace(str(staging_output), str(output_path))


def cleanup_auxiliary_outputs(output_path: Path) -> None:
    """Best-effort cleanup so a successful run leaves one final PDF only."""
    output_path = Path(output_path).expanduser().resolve()
    parent = output_path.parent
    stem = output_path.stem
    patterns = [
        f"{stem}.processing_*.tmp.pdf",
        f"{stem}.tmp.pdf",
        f"{stem}_tmp.pdf",
        f"{stem}_qc_original_*.pdf",
        f"{stem}_qc*.csv",
        f"{stem}_quality*.csv",
        f"{stem}_debug_masks",
        f"{stem}_debug_masks*",
    ]
    for pattern in patterns:
        for candidate in parent.glob(pattern):
            try:
                if candidate.resolve() == output_path:
                    continue
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
                else:
                    candidate.unlink(missing_ok=True)
            except Exception:
                pass
    # Also remove QC originals created from same-input overwrite cases.
    for candidate in parent.glob("*_qc_original_*.pdf"):
        try:
            candidate.unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_non_result_job_artifacts(job: "JobState", final_outputs: list[Path]) -> None:
    """Remove app-owned temporary artifacts while never touching user source folders."""
    protected = {Path(path).expanduser().resolve() for path in final_outputs}
    root = Path(job.output_root).expanduser().resolve()
    try:
        owned = root == OUTPUT_DIR.resolve() or OUTPUT_DIR.resolve() in root.parents
    except Exception:
        owned = False
    if not owned or not root.exists():
        return
    for candidate in root.iterdir():
        try:
            resolved = candidate.resolve()
            if resolved in protected:
                continue
            name = candidate.name.lower()
            is_artifact = (
                name.endswith(".tmp.pdf")
                or ".processing_" in name
                or "_qc_original_" in name
                or "_qc" in name
                or "_quality" in name
                or "debug" in name
            )
            if candidate.suffix.lower() == ".pdf":
                is_artifact = True
            if is_artifact:
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
                else:
                    candidate.unlink(missing_ok=True)
        except Exception:
            pass


@dataclass
class JobState:
    id: str
    upload_dir: Path
    output_root: Path
    status: str = "queued"
    percent: float = 0.0
    status_text: str = "Đang chờ xử lý..."
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    outputs: list[Path] = field(default_factory=list)
    uploaded_files: list[Path] = field(default_factory=list)
    qc_report: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=now_iso)
    done_at: str | None = None
    thread: threading.Thread | None = None
    next_event_id: int = 1

    def emit(self, event_type: str, message: str = "", level: str = "info", **data: Any) -> None:
        event = {
            "id": self.next_event_id,
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "level": level,
            "message": message,
            "status": self.status,
            "percent": round(float(self.percent), 2),
            **jsonable(data),
        }
        self.next_event_id += 1
        self.events.append(event)
        self.events[:] = self.events[-MAX_RETAINED_EVENTS:]
        if event_type in {"log", "error", "status", "done", "qc"} and message:
            write_tech_log(f"job={self.id} {level.upper()} {message}")


JOBS: dict[str, JobState] = {}


def _qc_settings(config: dict[str, Any]) -> dict[str, Any]:
    qc = dict(config.get("qc", {}))
    mean_raw = qc.get("mean_abs_diff_max", None)
    mode_raw = str(qc.get("mode", "full") or "full").strip().lower()
    if mode_raw not in {"full", "quick", "off"}:
        mode_raw = "full"
    return {
        "dpi": int(qc.get("dpi", 150)),
        "fail_on_visual_loss": bool(qc.get("fail_on_visual_loss", False)),
        "dark_lost_ratio_max": float(qc.get("dark_lost_ratio_max", 0.025)),
        "high_contrast_lost_ratio_max": float(qc.get("high_contrast_lost_ratio_max", 0.025)),
        "mean_abs_diff_max": None if mean_raw in {None, "", "null", "None"} else float(mean_raw),
        "workers": 1,
        "mode": mode_raw,
        "quick_max_pages": int(qc.get("quick_max_pages", 12) or 12),
    }


def _make_qc_reference_pdf(input_pdf: Path, output_path: Path) -> tuple[Path, Path | None]:
    try:
        same_path = input_pdf.expanduser().resolve() == output_path.expanduser().resolve()
    except Exception:
        same_path = False
    if not same_path:
        return input_pdf, None
    tmp_path = output_path.parent / f"{input_pdf.stem}_qc_original_{uuid.uuid4().hex[:8]}.pdf"
    shutil.copy2(str(input_pdf), str(tmp_path))
    return tmp_path, tmp_path


def _build_output_paths(job: JobState, input_files: list[Path], *, same_folder: bool, overwrite: bool, suffix: str, output_dir: str | None) -> list[Path]:
    outputs: list[Path] = []
    suffix = suffix if suffix is not None else "_clean"
    if not suffix and not overwrite:
        suffix = "_clean"

    custom_root: Path | None = None
    if (not same_folder) and (not overwrite) and output_dir:
        custom_root = Path(output_dir).expanduser().resolve()
        custom_root.mkdir(parents=True, exist_ok=True)

    for input_pdf in input_files:
        if overwrite:
            outputs.append(input_pdf)
        elif same_folder:
            outputs.append(unique_path(input_pdf.parent, f"{input_pdf.stem}{suffix}.pdf"))
        else:
            root = custom_root or job.output_root
            root.mkdir(parents=True, exist_ok=True)
            outputs.append(unique_path(root, f"{input_pdf.stem}{suffix}.pdf"))
    return outputs


def _prepare_mode_config(manager: ConfigManager, mode: str, preset: str, output_grayscale: bool, ly_aggressive_white: bool) -> dict[str, Any]:
    mode_config = dict(manager.get_mode_config(mode))
    mode_config.pop("display_name", None)
    mode_config["output_grayscale"] = bool(output_grayscale)

    if mode in {"ly", "ipclass"}:
        mode_config["aggressive_white"] = bool(ly_aggressive_white)
    if mode in {"toan", "tdm"}:
        mode_config["graph_safe_paper_mode"] = True
        mode_config["mode"] = "quality" if preset in {"high_quality", "quality"} else "balanced"
    return mode_config


def _run_qc(
    job: JobState,
    reference_pdf: Path,
    staging_pdf: Path,
    config: dict[str, Any],
    *,
    require_text_preservation: bool = True,
    allowed_removed_text_markers: list[str] | None = None,
    allowed_text_loss_pages: list[int] | None = None,
) -> dict[str, Any]:
    qc_settings = _qc_settings(config)
    if str(qc_settings.get("mode", "full")).lower() == "off":
        report = {
            "rows": [],
            "summary": {
                "ok": True,
                "page_count_ok": True,
                "pages_checked": 0,
                "failed_pages": [],
                "qc_mode": "off",
                "warning": "QC đã bị tắt theo cấu hình; output không được kiểm tra tự động.",
            },
        }
        job.qc_report = report
        job.emit("qc", "QC: đã tắt theo cấu hình.", level="warning", summary=report["summary"])
        return report

    mode_label = "đầy đủ" if qc_settings.get("mode") == "full" else "nhanh/lấy mẫu"
    job.status_text = f"Đang chạy quality_check_pdf ({mode_label}) cho {staging_pdf.name}..."
    job.emit("status", job.status_text)
    report = run_quality_check(
        reference_pdf,
        staging_pdf,
        require_text_preservation=require_text_preservation,
        allowed_removed_text_markers=allowed_removed_text_markers,
        allowed_text_loss_pages=allowed_text_loss_pages,
        **qc_settings,
    )
    job.qc_report = report
    summary = dict(report.get("summary", {}))
    job.emit(
        "qc",
        f"QC: ok={summary.get('ok')} mode={summary.get('qc_mode')} workers={summary.get('qc_workers')} pages_checked={summary.get('pages_checked')}/{summary.get('pages_total')} failed={summary.get('failed_pages')}",
        level="success" if summary.get("ok") else "error",
        summary=summary,
    )
    if not summary.get("ok", False):
        raise RuntimeError(f"quality_check_pdf không đạt: {summary}")
    return report


def _process_single_pdf(
    job: JobState,
    *,
    input_pdf: Path,
    output_path: Path,
    mode: str,
    mode_config: dict[str, Any],
    run_config: dict[str, Any],
    file_index: int,
    file_total: int,
    config_data: dict[str, Any],
) -> None:
    qc_reference_pdf, qc_temp_pdf = _make_qc_reference_pdf(input_pdf, output_path)
    staging_output = make_processing_output_path(output_path)
    qc_report: dict[str, Any] | None = None
    processing_report: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None

    try:
        try:
            preflight = preflight_pdf(input_pdf, mode, mode_config)
            warnings = preflight.get("warnings") or []
            job.emit(
                "log",
                f"Preflight: pages={preflight.get('pages')}, text_chars={preflight.get('text_chars')}, links={preflight.get('links')}, annots={preflight.get('annots')}, full_raster={preflight.get('full_raster_output')}",
            )
            for warning in warnings:
                job.emit("log", f"Preflight warning: {warning}", level="warning")
        except Exception as exc:
            job.emit("log", f"Preflight warning: không đọc được metadata trước xử lý ({exc}).", level="warning")

        if preflight is not None:
            try:
                mode_config["_preflight_text_chars"] = int(preflight.get("text_chars") or 0)
            except Exception:
                pass

        require_text_preservation = not bool(preflight and preflight.get("full_raster_output"))
        if not require_text_preservation and preflight and int(preflight.get("text_chars") or 0) > 0:
            job.emit(
                "log",
                "QC: mode full-raster sẽ chuyển lớp chữ thành ảnh; vẫn kiểm tra số trang, kích thước và mất nội dung trực quan.",
                level="warning",
            )

        backup_on_promote = input_pdf.expanduser().resolve() == output_path.expanduser().resolve()

        if mode == "hoa":
            markers = mode_config.get("markers") or PRESETS["TaiLieuOnThi (khuyên dùng)"]
            if isinstance(markers, str):
                markers = [x.strip() for x in markers.splitlines() if x.strip()]
            allow_stream_auto = bool(mode_config.get("allow_stream_auto", False))
            job.status_text = f"File {file_index}/{file_total}: đang đọc/xử lý stream PDF..."
            job.emit("status", job.status_text)
            job.emit("log", "TYHH stream-only: không render ảnh, không inpaint.")
            processing_report = clean_pdf_stream_safe(
                input_pdf=input_pdf,
                output_pdf=staging_output,
                markers=list(markers),
                allow_stream_auto=allow_stream_auto,
                log=lambda s: job.emit("log", str(s)),
                should_cancel=lambda: job.cancel_event.is_set(),
            )
            job.emit("log", f"TYHH stream result: removed={processing_report['removed_streams']}, changed_pages={processing_report['changed_pages']}, skipped={processing_report['skipped_streams']}")
            qc_report = _run_qc(
                job,
                qc_reference_pdf,
                staging_output,
                config_data,
                require_text_preservation=require_text_preservation,
                allowed_removed_text_markers=list(markers),
            )
            promote_processed_pdf(staging_output, output_path, backup_existing=backup_on_promote)
            job.emit("log", f"QC pass; promoted output: {output_path}", level="success")
            return

        dpi = int(run_config["dpi"])
        output_dpi = int(run_config["output_dpi"])
        quality = int(run_config["quality"])
        cpu = int(run_config.get("cpu", 0))

        def progress(done: int, total: int, msg: str) -> None:
            inner = done / max(1, total)
            job.percent = ((file_index - 1) + inner) / max(1, file_total) * 100
            job.status_text = f"File {file_index}/{file_total} • Trang {done}/{total}: {msg or 'đang xử lý'}"
            job.emit(
                "progress",
                job.status_text,
                percent=job.percent,
                file_index=file_index,
                file_total=file_total,
                page_done=done,
                page_total=total,
            )

        job.status_text = f"File {file_index}/{file_total}: đang đọc PDF và render trang..."
        job.emit("status", job.status_text)
        processing_report = process_pdf_optimized(
            input_pdf=input_pdf,
            output_pdf=staging_output,
            mode=mode,
            mode_config=mode_config,
            dpi=dpi,
            output_dpi=output_dpi,
            quality=quality,
            workers=cpu,
            log=lambda s: job.emit("log", str(s)),
            progress=progress,
            should_cancel=lambda: job.cancel_event.is_set(),
        )
        if processing_report.get("warnings"):
            job.emit("log", f"Cảnh báo: {len(processing_report['warnings'])} trang dùng fallback ảnh gốc.", level="warning")
        rasterized_pages = [int(page) for page in (processing_report.get("full_raster_pages") or [])]
        if rasterized_pages and require_text_preservation:
            job.emit(
                "log",
                f"QC: cho phép mất text layer trên các trang đã rasterize có chủ đích: {rasterized_pages}.",
                level="warning",
            )
        qc_report = _run_qc(
            job,
            qc_reference_pdf,
            staging_output,
            config_data,
            require_text_preservation=require_text_preservation,
            allowed_text_loss_pages=rasterized_pages,
        )
        promote_processed_pdf(staging_output, output_path, backup_existing=backup_on_promote)
        if backup_on_promote:
            job.emit("log", "Đã tạo backup bản gốc trong thư mục _backup trước khi ghi đè.", level="warning")
        job.emit("log", f"QC pass; promoted output: {output_path}", level="success")
    except Exception:
        if staging_output.exists():
            cleanup_staging_output(staging_output)
            job.emit(
                "log",
                "Output tạm đã được xóa vì lỗi/QC fail; không tạo thư mục debug.",
                level="error",
            )
        raise
    finally:
        cleanup_staging_output(staging_output)
        if qc_temp_pdf is not None:
            try:
                qc_temp_pdf.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            cleanup_auxiliary_outputs(output_path)
        except Exception:
            pass



def _resolve_run_config(preset: str, params: dict[str, Any]) -> dict[str, Any]:
    preset_key = str(preset or "balanced").strip().lower()
    if preset_key not in PRESET_ALIASES:
        preset_key = "balanced"
    values = PRESET_ALIASES[preset_key]

    def _value(name: str, default: int) -> int:
        raw = params.get(name)
        return int(default if raw in {None, ""} else raw)

    return {
        "quality_profile": preset_key,
        "dpi": _value("dpi", values["dpi"]),
        "output_dpi": _value("output_dpi", values["output_dpi"]),
        "quality": _value("quality", values["quality"]),
        # Hard requirement for this personal desktop build: no worker Python processes.
        "cpu": 1,
    }

def process_job(job_id: str, params: dict[str, Any]) -> None:
    job = JOBS[job_id]
    try:
        job.status = "running"
        job.percent = 0
        job.emit("status", "Đang nạp bộ xử lý PDF...")
        load_processing_engine()
        job.emit("status", "Đang chuẩn bị xử lý PDF...")

        mode = str(params.get("mode") or "ly").strip().lower()
        if mode not in DOCUMENT_TYPES:
            raise ValueError(f"Mode không hợp lệ: {mode}")
        preset = str(params.get("preset") or "balanced").strip().lower()
        if preset not in PRESET_ALIASES:
            preset = "balanced"

        with CONFIG_LOCK:
            manager = config_manager()
            config_data = manager.data
        run_config = _resolve_run_config(preset, params)
        preset = str(run_config["quality_profile"])
        mode_config = _prepare_mode_config(
            manager,
            mode,
            preset,
            bool(params.get("output_grayscale", False)),
            bool(params.get("ly_aggressive_white", False)),
        )
        speed_config = dict(config_data.get("speed", {}))
        if bool(speed_config.get("fast_lossless_png", True)):
            # PNG compression 0 is still lossless; it trades larger files for faster encoding.
            mode_config["png_compression"] = 0
        if bool(speed_config.get("skip_preredact_when_no_text", True)):
            mode_config["skip_tailieuonthi_preredact_when_no_text"] = True
        if bool(speed_config.get("batch_page_logs", True)):
            mode_config["log_every_pages"] = int(speed_config.get("log_every_pages", 5) or 5)

        total_files = len(job.uploaded_files)
        output_paths = _build_output_paths(
            job,
            job.uploaded_files,
            same_folder=bool(params.get("same_folder", True)),
            overwrite=bool(params.get("overwrite", False)),
            suffix=str(params.get("suffix") or "_clean"),
            output_dir=str(params.get("output_dir") or "").strip() or None,
        )

        job.emit("log", "Bắt đầu xử lý.")
        job.emit("log", f"Ứng dụng: PDF_Cleaner")
        job.emit("log", f"Mode: {DOCUMENT_TYPES.get(mode, mode)}")
        job.emit("log", f"Số file: {total_files}")
        extra_flags = ""
        if mode == "ly":
            extra_flags = f" | Aggressive={bool(mode_config.get('aggressive_white', False))}"
        job.emit("log", f"Profile={preset} | DPI={run_config['dpi']} | Output DPI={run_config['output_dpi']} | JPEG={run_config['quality']} | CPU={run_config['cpu']} | PNG compression={mode_config.get('png_compression', 'config')} | Grayscale={bool(mode_config.get('output_grayscale', False))}{extra_flags}")

        completed: list[Path] = []
        for idx, input_pdf in enumerate(job.uploaded_files, start=1):
            if job.cancel_event.is_set():
                job.emit("log", "Đã hủy trước khi xử lý file tiếp theo.", level="warning")
                break
            output_path = output_paths[idx - 1]
            job.status_text = f"File {idx}/{total_files}: đang xử lý {input_pdf.name}..."
            job.emit("status", job.status_text, file_index=idx, file_total=total_files)
            job.emit("log", f"File {idx}/{total_files}: {input_pdf}")
            job.emit("log", f"Output: {output_path}")
            _process_single_pdf(
                job,
                input_pdf=input_pdf,
                output_path=output_path,
                mode=mode,
                mode_config=mode_config,
                run_config=run_config,
                file_index=idx,
                file_total=total_files,
                config_data=config_data,
            )
            if output_path.exists():
                completed.append(output_path)
                job.outputs = completed[:]
                job.emit("log", f"Xuất xong: {output_path}", level="success")
            job.percent = idx / max(1, total_files) * 100
            job.emit("progress", f"Hoàn tất file {idx}/{total_files}", percent=job.percent)

        job.outputs = completed
        try:
            cleanup_non_result_job_artifacts(job, completed)
        except Exception:
            pass
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.status_text = f"Đã hủy. Đã xuất {len(completed)} file trước khi dừng."
            job.emit("done", job.status_text, level="warning", outputs=[str(p) for p in completed])
        else:
            job.status = "done"
            job.percent = 100
            job.status_text = f"Hoàn tất • Đã xuất {len(completed)} file."
            job.emit("done", job.status_text, level="success", outputs=[str(p) for p in completed])
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        job.status_text = "Có lỗi trong quá trình xử lý. Xem log kỹ thuật để biết chi tiết."
        detail = traceback.format_exc()
        write_tech_log(f"job={job.id} TRACEBACK\n{detail}")
        job.emit("error", f"Lỗi: {exc}", level="error", detail=detail)
    finally:
        job.done_at = now_iso()




def get_config() -> dict[str, Any]:
    with CONFIG_LOCK:
        manager = config_manager()
        data = manager.data
    return {
        "app_name": "PDF_Cleaner",
        "config": jsonable(data),
        "presets": PRESET_ALIASES,
        "document_types": DOCUMENT_TYPES,
    }


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    with CONFIG_LOCK:
        manager = config_manager()
        common = payload.get("common")
        mode = payload.get("mode")
        mode_values = payload.get("mode_values")
        qc_values = payload.get("qc")
        speed_values = payload.get("speed")
        if isinstance(common, dict):
            manager.update_common(common)
        if isinstance(mode, str) and isinstance(mode_values, dict):
            manager.update_mode(mode, mode_values)
        if isinstance(qc_values, dict):
            manager.data.setdefault("qc", {}).update(qc_values)
            manager.save()
        if isinstance(speed_values, dict):
            manager.data.setdefault("speed", {}).update(speed_values)
            manager.save()
        manager.load()
        common_out = manager.data.get("common", {}) if isinstance(manager.data, dict) else {}
    return {
        "ok": True,
        "message": "Đã lưu cấu hình mặc định.",
        "overwrite": bool(common_out.get("overwrite", False)),
        "same_folder": bool(common_out.get("same_folder", True)),
        "suffix": str(common_out.get("suffix", "_clean")),
    }


def reset_settings() -> dict[str, Any]:
    with CONFIG_LOCK:
        manager = config_manager()
        data = manager.reset()
    return {"ok": True, "config": jsonable(data)}


def _parse_bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int_value(value: Any, default: int | None = None) -> int | None:
    if value in {None, ""}:
        return default
    return int(value)


def local_pdf_info(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except Exception:
        size = 0
    return {
        "path": str(path),
        "name": path.name,
        "size": size,
        "parent_dir": str(path.parent),
    }


def validate_local_pdf_path(raw_path: Any) -> Path:
    raw = str(raw_path or "").strip().strip('"')
    if not raw:
        raise ValueError("Đường dẫn PDF rỗng.")
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"File không tồn tại: {path}")
    if not path.is_file():
        raise ValueError(f"Không phải file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Không phải file PDF: {path}")
    return path


def _prune_jobs() -> None:
    with JOBS_LOCK:
        if len(JOBS) < MAX_JOB_HISTORY:
            return
        terminal = [job for job in JOBS.values() if job.status in {"done", "error", "cancelled"}]
        terminal.sort(key=lambda item: item.created_at)
        while len(JOBS) >= MAX_JOB_HISTORY and terminal:
            old = terminal.pop(0)
            JOBS.pop(old.id, None)


def start_process_local(payload: dict[str, Any]) -> dict[str, Any]:
    raw_paths = payload.get("paths") or payload.get("files") or []
    if not isinstance(raw_paths, list):
        raise ValueError("paths phải là danh sách đường dẫn PDF.")

    pdf_paths: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = validate_local_pdf_path(raw)
        key = str(path).lower() if os.name == "nt" else str(path)
        if key not in seen:
            pdf_paths.append(path)
            seen.add(key)
    if not pdf_paths:
        raise ValueError("Hãy chọn ít nhất một file PDF.")

    _prune_jobs()
    job_id = uuid.uuid4().hex[:12]
    output_root = OUTPUT_DIR / job_id
    job = JobState(id=job_id, upload_dir=APP_DATA_DIR / "local_paths", output_root=output_root)
    job.uploaded_files = pdf_paths
    params = {
        "mode": str(payload.get("mode") or "ly"),
        "preset": str(payload.get("preset") or "balanced"),
        "same_folder": _parse_bool_value(payload.get("same_folder"), True),
        "overwrite": _parse_bool_value(payload.get("overwrite"), False),
        "suffix": str(payload.get("suffix") or "_clean"),
        "output_dir": str(payload.get("output_dir") or "").strip(),
        "dpi": _parse_int_value(payload.get("dpi")),
        "output_dpi": _parse_int_value(payload.get("output_dpi")),
        "quality": _parse_int_value(payload.get("quality")),
        # Accepted for backward-compatible UI payloads but ignored by _resolve_run_config.
        "cpu": _parse_int_value(payload.get("cpu")),
        "output_grayscale": _parse_bool_value(payload.get("output_grayscale"), False),
        "ly_aggressive_white": _parse_bool_value(payload.get("ly_aggressive_white"), False),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    job.emit("log", f"Đã nhận {len(pdf_paths)} file PDF trên máy.")
    if params["overwrite"]:
        job.emit("log", "Ghi đè đang bật: staging → QC → backup _backup → thay file gốc.", level="warning")
    worker = threading.Thread(target=process_job, args=(job_id, params), daemon=True, name=f"pdfcleaner-{job_id}")
    job.thread = worker
    worker.start()
    return {"job_id": job_id, "status": job.status, "source": "local_path"}


def poll_job(job_id: str, after_event_id: int = 0) -> dict[str, Any]:
    job = JOBS.get(str(job_id))
    if job is None:
        raise ValueError("Không tìm thấy tác vụ xử lý.")
    cursor = int(after_event_id or 0)
    events = [event for event in list(job.events) if int(event.get("id", 0)) > cursor]
    last_event_id = int(events[-1]["id"]) if events else cursor
    return {
        "events": events,
        "last_event_id": last_event_id,
        "status": job.status,
        "terminal": job.status in {"done", "error", "cancelled"},
    }


def cancel_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(str(job_id))
    if job is None:
        raise ValueError("Không tìm thấy tác vụ xử lý.")
    if job.status in {"done", "error", "cancelled"}:
        return {"ok": True, "status": job.status}
    job.cancel_event.set()
    job.emit("status", "Đã gửi yêu cầu hủy. Tool sẽ dừng ở điểm an toàn tiếp theo.", level="warning")
    return {"ok": True, "status": job.status}


def _latest_output(job_id: str) -> Path:
    job = JOBS.get(str(job_id))
    if job is None or not job.outputs:
        raise ValueError("Chưa có file kết quả.")
    path = Path(job.outputs[-1]).expanduser().resolve()
    if not path.exists():
        raise ValueError("File kết quả không còn tồn tại.")
    return path


def open_path(path: Path) -> None:
    import subprocess

    path = Path(path).expanduser().resolve()
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_latest_file(job_id: str) -> dict[str, Any]:
    path = _latest_output(job_id)
    open_path(path)
    return {"ok": True, "path": str(path)}


def open_latest_folder(job_id: str) -> dict[str, Any]:
    path = _latest_output(job_id).parent
    open_path(path)
    return {"ok": True, "path": str(path)}


def open_log() -> dict[str, Any]:
    TECH_LOG.touch(exist_ok=True)
    open_path(TECH_LOG)
    return {"ok": True, "path": str(TECH_LOG)}


def open_tool_dir() -> dict[str, Any]:
    open_path(ROOT_DIR)
    return {"ok": True, "path": str(ROOT_DIR)}
