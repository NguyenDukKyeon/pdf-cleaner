from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import fitz
import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _page_count(path: Path) -> int:
    with fitz.open(str(path)) as doc:
        return int(doc.page_count)


def _worker_v2(input_pdf: Path, output_pdf: Path, profile: str) -> dict[str, Any]:
    from backend.app.processing_service import process_document_v2

    result = process_document_v2(
        input_pdf,
        output_pdf,
        options={
            "content_profile": "auto",
            "allow_legacy_fallback": False,
            "workers": 0,
            "qc_dpi": 96,
            "max_outside_change_ratio": 0.08,
            "max_watermark_residual_score": 0.08,
        },
    )
    report = result.report.as_dict()
    qc = result.qc.as_dict()
    return {
        "mode": "v2",
        "profile": profile,
        "strategy": report.get("strategy"),
        "used_fallback": report.get("used_fallback"),
        "analysis_seconds": report.get("analysis_seconds"),
        "processing_seconds": report.get("processing_seconds"),
        "qc_seconds": report.get("qc_seconds"),
        "total_seconds": report.get("total_seconds"),
        "worker_count": report.get("worker_count"),
        "native_image_pages": report.get("native_image_pages"),
        "rasterized_pages": report.get("rasterized_pages"),
        "ocr_calls": report.get("ocr_calls"),
        "watermark_residual_score": qc.get("watermark_residual_score"),
        "outside_change_ratio": qc.get("outside_change_ratio"),
        "changed_pixel_ratio": qc.get("changed_pixel_ratio"),
        "qc_ok": qc.get("ok"),
        "repair_engine": (report.get("metadata") or {}).get("repair_engine"),
    }


def _worker_legacy(input_pdf: Path, output_pdf: Path, profile: str) -> dict[str, Any]:
    from backend.engine.router.models import ProcessingPlan, StrategyKind
    from backend.engine.strategies.legacy import LegacyStrategy

    plan = ProcessingPlan(
        strategy=StrategyKind.LEGACY,
        confidence=1.0,
        content_profile=profile,
        reason="Task 10 legacy benchmark",
    )
    started = time.perf_counter()
    result = LegacyStrategy().execute(
        input_pdf,
        output_pdf,
        plan,
        workers=0,
        dpi=240,
        output_dpi=240,
        quality=92,
    )
    elapsed = time.perf_counter() - started
    return {
        "mode": "legacy",
        "profile": profile,
        "strategy": "legacy",
        "used_fallback": None,
        "analysis_seconds": None,
        "processing_seconds": elapsed,
        "qc_seconds": None,
        "total_seconds": elapsed,
        "worker_count": None,
        "native_image_pages": None,
        "rasterized_pages": int(getattr(result, "rasterized_pages", 0)),
        "ocr_calls": None,
        "watermark_residual_score": None,
        "outside_change_ratio": None,
        "changed_pixel_ratio": None,
        "qc_ok": None,
        "repair_engine": "legacy_subject_adapter",
    }


def _run_worker(args: argparse.Namespace) -> int:
    input_pdf = Path(args.input).expanduser().resolve()
    output_pdf = Path(args.output).expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    if output_pdf.exists():
        output_pdf.unlink()
    if args.worker_mode == "v2":
        payload = _worker_v2(input_pdf, output_pdf, args.profile)
    else:
        payload = _worker_legacy(input_pdf, output_pdf, args.profile)
    pages = _page_count(input_pdf)
    payload.update(
        {
            "input": str(input_pdf),
            "output": str(output_pdf),
            "pages": pages,
            "seconds_per_page": float(payload["total_seconds"]) / max(1, pages),
            "input_bytes": input_pdf.stat().st_size,
            "output_bytes": output_pdf.stat().st_size,
            "output_size_ratio": output_pdf.stat().st_size / max(1, input_pdf.stat().st_size),
        }
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _rss_tree_bytes(process: psutil.Process) -> int:
    total = 0
    procs = [process]
    try:
        procs.extend(process.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    for proc in procs:
        try:
            total += int(proc.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _measure(mode: str, input_pdf: Path, output_pdf: Path, profile: str) -> dict[str, Any]:
    env = os.environ.copy()
    old_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + old_path if old_path else "")
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-mode",
        mode,
        "--input",
        str(input_pdf),
        "--output",
        str(output_pdf),
        "--profile",
        profile,
    ]
    started = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ps_proc = psutil.Process(proc.pid)
    peak = 0
    while proc.poll() is None:
        peak = max(peak, _rss_tree_bytes(ps_proc))
        time.sleep(0.05)
    peak = max(peak, _rss_tree_bytes(ps_proc))
    stdout, stderr = proc.communicate()
    wall = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(f"{mode} benchmark failed: {stderr[-3000:]}")
    lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise RuntimeError(f"{mode} benchmark produced no JSON: {stdout[-2000:]}")
    payload = json.loads(lines[-1])
    payload["wall_seconds"] = wall
    payload["peak_ram_bytes"] = peak
    payload["peak_ram_mb"] = peak / (1024 * 1024)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-mode", choices=("v2", "legacy"))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--output-dir")
    parser.add_argument("--profile", default="auto", choices=("auto", "math", "physics", "chemistry", "ebook"))
    parser.add_argument("--label", default="case")
    parser.add_argument("--json")
    args = parser.parse_args(argv)

    if args.worker_mode:
        if not args.output:
            parser.error("--output is required in worker mode")
        return _run_worker(args)

    input_pdf = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir or (ROOT / ".benchmark-output")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    v2 = _measure("v2", input_pdf, output_dir / f"{args.label}-v2.pdf", args.profile)
    legacy = _measure("legacy", input_pdf, output_dir / f"{args.label}-legacy.pdf", args.profile)
    result = {"label": args.label, "input": str(input_pdf), "v2": v2, "legacy": legacy}
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
