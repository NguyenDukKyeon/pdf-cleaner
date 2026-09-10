from __future__ import annotations
from dataclasses import dataclass, replace
from pathlib import Path
import time
from typing import Any, Callable
from backend.engine.pipeline_v2.analyze import analyze_document
from backend.engine.pipeline_v2.plan import plan_document
from backend.engine.pipeline_v2.execute import execute_plan
from backend.engine.pipeline_v2.report import ProcessingReport
from backend.engine.qc_v2.validator import QCReport, validate_output
from .jobs import JobStage
from .output_service import atomic_promote, cleanup_path, make_staging_path

@dataclass(frozen=True, slots=True)
class DocumentProcessResult:
    profile: Any; plan: Any; report: ProcessingReport; qc: QCReport; backup_path: str | None=None

def _emit(callback, stage: JobStage, **data):
    if callback is not None: callback(stage, **data)

def process_document_v2(input_path: str | Path, final_path: str | Path, *, options: dict[str,Any] | None=None, callbacks: dict[str,Any] | None=None, on_stage: Callable[...,None] | None=None, backup_existing: bool=False) -> DocumentProcessResult:
    input_path=Path(input_path).expanduser().resolve(); final_path=Path(final_path).expanduser().resolve(); options=dict(options or {}); callbacks=dict(callbacks or {})
    staging=make_staging_path(final_path); started=time.perf_counter()
    try:
        _emit(on_stage,JobStage.ANALYZING); t=time.perf_counter(); profile=analyze_document(input_path,options); analysis_seconds=time.perf_counter()-t
        _emit(on_stage,JobStage.PLANNING,profile=profile); plan=plan_document(profile,options)
        _emit(on_stage,JobStage.PROCESSING,profile=profile,plan=plan)
        report=execute_plan(input_path,staging,plan,callbacks=callbacks,allow_legacy_fallback=bool(options.get("allow_legacy_fallback",True)),requested_workers=int(options.get("workers",0) or 0),dpi=int(options.get("dpi",240) or 240),output_dpi=int(options.get("output_dpi",options.get("dpi",240)) or 240),quality=int(options.get("quality",92) or 92))
        _emit(on_stage,JobStage.VERIFYING,profile=profile,plan=plan,report=report); t=time.perf_counter()
        qc=validate_output(input_path,staging,report,max_outside_change_ratio=float(options.get("max_outside_change_ratio",0.08)),dpi=int(options.get("qc_dpi",96) or 96)); qc_seconds=time.perf_counter()-t
        report=replace(report,analysis_seconds=analysis_seconds,qc_seconds=qc_seconds,total_seconds=time.perf_counter()-started,metadata={**dict(report.metadata or {}),"document_kind":getattr(profile.kind,"value",str(profile.kind)),"sampled_pages":list(profile.sampled_pages),"qc":qc.as_dict()})
        if not qc.ok: raise RuntimeError("QC failed: "+("; ".join(qc.reasons) or "unknown QC failure"))
        backup=atomic_promote(staging,final_path,backup_existing=backup_existing); report=replace(report,output_path=str(final_path),total_seconds=time.perf_counter()-started)
        _emit(on_stage,JobStage.DONE,profile=profile,plan=plan,report=report,qc=qc); return DocumentProcessResult(profile,plan,report,qc,str(backup) if backup else None)
    except Exception:
        cleanup_path(staging); raise
    finally: cleanup_path(staging)
