from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRESET_CONFIGS: dict[str, dict[str, Any]] = {
    "fast": {"dpi": 200, "output_dpi": 200, "quality": 88, "workers": 0},
    "balanced": {"dpi": 240, "output_dpi": 240, "quality": 92, "workers": 0},
    "high_quality": {"dpi": 320, "output_dpi": 320, "quality": 95, "workers": 0},
    "quality": {"dpi": 320, "output_dpi": 320, "quality": 95, "workers": 0},
    "safe_mode": {"dpi": 240, "output_dpi": 240, "quality": 95, "workers": 1},
    "safe": {"dpi": 240, "output_dpi": 240, "quality": 95, "workers": 1},
}


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, float]:
    """Computes medians across repeated runs for numerical metrics.

    Required medians:
    - median_total_seconds
    - median_pages_per_second
    - median_analyze_seconds
    - median_processing_seconds
    - median_qc_seconds
    """
    if not runs:
        return {}

    summary: dict[str, float] = {}

    total_seconds_list = [
        float(r["total_seconds"]) for r in runs if r.get("total_seconds") is not None
    ]
    if total_seconds_list:
        summary["median_total_seconds"] = float(statistics.median(total_seconds_list))

    pps_list: list[float] = []
    for r in runs:
        if r.get("pages_per_second") is not None:
            pps_list.append(float(r["pages_per_second"]))
        elif r.get("pages") is not None and r.get("total_seconds") is not None:
            tot = float(r["total_seconds"])
            if tot > 0:
                pps_list.append(float(r["pages"]) / tot)
    if pps_list:
        summary["median_pages_per_second"] = float(statistics.median(pps_list))

    metric_keys = [
        "analyze_seconds",
        "processing_seconds",
        "qc_seconds",
        "output_size_bytes",
        "footer_residual_score",
        "watermark_residual_score",
        "outside_change_ratio",
    ]
    for key in metric_keys:
        vals = [float(r[key]) for r in runs if r.get(key) is not None]
        if vals:
            summary[f"median_{key}"] = float(statistics.median(vals))

    return summary


def run_single_benchmark(
    input_path: Path | str,
    output_path: Path | str,
    preset: str = "balanced",
    extra_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from backend.app.processing_service import process_document_v2

    input_path = Path(input_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()

    if input_path == output_path:
        raise ValueError(f"Output path cannot be identical to source PDF: {input_path}")

    preset_key = preset.strip().lower()
    preset_cfg = PRESET_CONFIGS.get(preset_key, PRESET_CONFIGS["balanced"])

    options: dict[str, Any] = {
        "content_profile": "auto",
        "engine_preference": "auto_smart",
        "footer_cleanup": "auto",
        "allow_legacy_fallback": True,
        "dpi": int(preset_cfg["dpi"]),
        "output_dpi": int(preset_cfg["output_dpi"]),
        "quality": int(preset_cfg["quality"]),
        "workers": int(preset_cfg["workers"]),
        "qc_dpi": 96,
        "max_outside_change_ratio": 0.08,
        "max_watermark_residual_score": 0.08,
    }
    if extra_options:
        options.update(extra_options)

    with fitz.open(str(input_path)) as doc:
        pages = int(doc.page_count)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    result = process_document_v2(input_path, output_path, options=options)

    report = result.report
    qc = result.qc

    output_size_bytes = output_path.stat().st_size if output_path.exists() else 0
    total_seconds = float(report.total_seconds)
    pages_per_second = float(pages / total_seconds) if total_seconds > 0 else 0.0

    return {
        "analyze_seconds": float(report.analysis_seconds),
        "processing_seconds": float(report.processing_seconds),
        "qc_seconds": float(report.qc_seconds),
        "total_seconds": total_seconds,
        "pages": pages,
        "pages_per_second": pages_per_second,
        "strategy": str(report.strategy),
        "confidence": float(report.confidence),
        "worker_count": int(report.worker_count),
        "native_image_pages": int(report.native_image_pages),
        "ocr_calls": int(report.ocr_calls),
        "output_size_bytes": output_size_bytes,
        "footer_residual_score": (
            float(qc.footer_residual_score)
            if qc.footer_residual_score is not None
            else None
        ),
        "watermark_residual_score": (
            float(qc.watermark_residual_score)
            if qc.watermark_residual_score is not None
            else None
        ),
        "outside_change_ratio": float(qc.outside_change_ratio),
        "input": str(input_path),
        "output": str(output_path),
        "preset": preset,
        "qc_ok": bool(qc.ok),
    }


def run_benchmark(
    pdf_paths: list[Path | str],
    preset: str = "balanced",
    repeat: int = 3,
    output_dir: Path | str = ".benchmark-output",
    extra_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    all_runs: list[dict[str, Any]] = []
    files_data: dict[str, Any] = {}

    resolved_paths = [Path(p).expanduser().resolve() for p in pdf_paths]
    stem_counts = collections.Counter(p.stem for p in resolved_paths)

    for pdf_path in resolved_paths:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        file_tag = pdf_path.stem
        if stem_counts[pdf_path.stem] > 1:
            parent_part = f"{pdf_path.parent.name}_" if pdf_path.parent.name else ""
            path_hash = hashlib.sha256(str(pdf_path).encode("utf-8")).hexdigest()[:6]
            file_tag = f"{parent_part}{pdf_path.stem}_{path_hash}"

        file_runs: list[dict[str, Any]] = []
        for run_idx in range(1, repeat + 1):
            run_output = out_dir / f"{file_tag}_{preset}_run{run_idx}.pdf"
            print(f"Running [{run_idx}/{repeat}] for {pdf_path.name}...")
            run_data = run_single_benchmark(
                pdf_path, run_output, preset=preset, extra_options=extra_options
            )
            run_data["run_index"] = run_idx
            file_runs.append(run_data)
            all_runs.append(run_data)

        files_data[str(pdf_path)] = {
            "runs": file_runs,
            "summary": summarize_runs(file_runs),
        }

    overall_summary = summarize_runs(all_runs)
    return {
        "preset": preset,
        "repeat": repeat,
        "runs": all_runs,
        "summary": overall_summary,
        "files": files_data,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V2 PDF Cleaner benchmark harness")
    parser.add_argument("pdfs", nargs="+", help="Path(s) to PDF file(s) to benchmark")
    parser.add_argument("--preset", default="balanced", help="Preset (default: balanced)")
    parser.add_argument(
        "--repeat", type=int, default=3, help="Number of repetitions per PDF (default: 3)"
    )
    parser.add_argument(
        "--output-dir", default=None, help="Directory to save outputs and reports"
    )
    parser.add_argument("--json", default=None, help="Custom output path for JSON report")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir or (ROOT / ".benchmark-output")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report_data = run_benchmark(
        pdf_paths=args.pdfs,
        preset=args.preset,
        repeat=args.repeat,
        output_dir=output_dir,
    )

    json_text = json.dumps(report_data, ensure_ascii=False, indent=2)
    report_file = (
        Path(args.json).expanduser().resolve()
        if args.json
        else (output_dir / "benchmark_report.json")
    )
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json_text, encoding="utf-8")

    # Also save preset-specific name if using default report filename
    if not args.json:
        preset_file = output_dir / f"benchmark_{args.preset}.json"
        preset_file.write_text(json_text, encoding="utf-8")

    print(f"\nBenchmark completed. Report written to {report_file}")
    print(f"Overall summary: {json.dumps(report_data['summary'], indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
