from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts.benchmark_v2 import main, run_benchmark, run_single_benchmark, summarize_runs
from tests.fixtures_factory import make_raster_pdf


def test_summarize_runs_reports_median_and_pages_per_second():
    summary = summarize_runs([
        {"total_seconds": 10.0, "pages": 20},
        {"total_seconds": 8.0, "pages": 20},
        {"total_seconds": 12.0, "pages": 20},
    ])
    assert summary["median_total_seconds"] == 10.0
    assert summary["median_pages_per_second"] == 2.0


def test_summarize_runs_reports_stage_medians():
    runs = [
        {
            "analyze_seconds": 1.2,
            "processing_seconds": 5.0,
            "qc_seconds": 0.8,
            "total_seconds": 7.0,
            "pages": 10,
            "pages_per_second": 10 / 7.0,
        },
        {
            "analyze_seconds": 1.0,
            "processing_seconds": 4.5,
            "qc_seconds": 0.7,
            "total_seconds": 6.2,
            "pages": 10,
            "pages_per_second": 10 / 6.2,
        },
        {
            "analyze_seconds": 1.5,
            "processing_seconds": 6.0,
            "qc_seconds": 1.0,
            "total_seconds": 8.5,
            "pages": 10,
            "pages_per_second": 10 / 8.5,
        },
    ]
    summary = summarize_runs(runs)
    assert summary["median_analyze_seconds"] == 1.2
    assert summary["median_processing_seconds"] == 5.0
    assert summary["median_qc_seconds"] == 0.8
    assert summary["median_total_seconds"] == 7.0
    assert pytest.approx(summary["median_pages_per_second"], rel=1e-3) == 10 / 7.0


def test_summarize_runs_empty():
    assert summarize_runs([]) == {}


def test_run_benchmark_captures_all_metrics(tmp_path: Path):
    src_pdf = tmp_path / "sample.pdf"
    make_raster_pdf(src_pdf, pages=2)
    out_dir = tmp_path / "out"

    report = run_benchmark([src_pdf], preset="balanced", repeat=1, output_dir=out_dir)

    assert len(report["runs"]) == 1
    run = report["runs"][0]

    required_keys = [
        "analyze_seconds",
        "processing_seconds",
        "qc_seconds",
        "total_seconds",
        "pages",
        "pages_per_second",
        "strategy",
        "confidence",
        "worker_count",
        "native_image_pages",
        "ocr_calls",
        "output_size_bytes",
        "footer_residual_score",
        "watermark_residual_score",
        "outside_change_ratio",
    ]
    for key in required_keys:
        assert key in run, f"Missing metric {key} in run data"

    assert run["pages"] == 2
    assert run["total_seconds"] > 0
    assert run["pages_per_second"] > 0
    assert run["output_size_bytes"] > 0
    assert "median_total_seconds" in report["summary"]
    assert "median_pages_per_second" in report["summary"]


def test_benchmark_main_cli(tmp_path: Path):
    src_pdf = tmp_path / "cli_sample.pdf"
    make_raster_pdf(src_pdf, pages=1)
    out_dir = tmp_path / "cli_out"
    json_path = out_dir / "custom_report.json"

    exit_code = main([
        str(src_pdf),
        "--preset",
        "balanced",
        "--repeat",
        "1",
        "--output-dir",
        str(out_dir),
        "--json",
        str(json_path),
    ])
    assert exit_code == 0
    assert json_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["preset"] == "balanced"
    assert len(payload["runs"]) == 1
    assert "summary" in payload
    assert "median_total_seconds" in payload["summary"]
