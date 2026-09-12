from pathlib import Path
import fitz
import pytest
from backend.app.jobs import JobStage
from backend.app.processing_service import process_document_v2
from backend.engine.pipeline_v2.report import ProcessingReport
from backend.engine.qc_v2.validator import QCReport
from backend.engine.router.models import ProcessingPlan, StrategyKind
from backend.engine.analyzer.models import DocumentKind, DocumentProfile


def _pdf(path: Path, text='BASE'):
    d=fitz.open(); p=d.new_page(); p.insert_text((72,72),text); d.save(path); d.close()


def test_failed_qc_keeps_final_untouched_and_cleans_staging(tmp_path, monkeypatch):
    src=tmp_path/'src.pdf'; final=tmp_path/'final.pdf'; _pdf(src); final.write_bytes(b'KEEP-ME')
    import backend.app.processing_service as svc
    monkeypatch.setattr(svc,'analyze_document',lambda *a,**k: DocumentProfile(1,DocumentKind.VECTOR,.99))
    monkeypatch.setattr(svc,'plan_document',lambda *a,**k: ProcessingPlan(StrategyKind.STREAM_REMOVE,.99))
    def fake_execute(inp,out,plan,**kwargs): Path(out).write_bytes(Path(inp).read_bytes()); return ProcessingReport('stream_remove',.99,str(out))
    monkeypatch.setattr(svc,'execute_plan',fake_execute)
    monkeypatch.setattr(svc,'validate_output',lambda *a,**k: QCReport(False,True,True,.0,(),('synthetic QC fail',)))
    with pytest.raises(RuntimeError,match='QC failed'): process_document_v2(src,final)
    assert final.read_bytes()==b'KEEP-ME'; assert not list(tmp_path.glob('*.v2stage*.pdf'))


def test_processing_service_emits_required_stages_and_promotes_atomically(tmp_path, monkeypatch):
    src=tmp_path/'src.pdf'; final=tmp_path/'final.pdf'; _pdf(src)
    import backend.app.processing_service as svc
    monkeypatch.setattr(svc,'analyze_document',lambda *a,**k: DocumentProfile(1,DocumentKind.VECTOR,.99))
    monkeypatch.setattr(svc,'plan_document',lambda *a,**k: ProcessingPlan(StrategyKind.STREAM_REMOVE,.99))
    def fake_execute(inp,out,plan,**kwargs): Path(out).write_bytes(Path(inp).read_bytes()); return ProcessingReport('stream_remove',.99,str(out),worker_count=2)
    monkeypatch.setattr(svc,'execute_plan',fake_execute); monkeypatch.setattr(svc,'validate_output',lambda *a,**k: QCReport(True,True,True,.0,(),()))
    stages=[]; result=process_document_v2(src,final,on_stage=lambda s,**data: stages.append(s))
    assert final.exists() and final.read_bytes().startswith(b'%PDF')
    assert stages==[JobStage.ANALYZING,JobStage.PLANNING,JobStage.PROCESSING,JobStage.VERIFYING,JobStage.DONE]
    assert result.report.worker_count==2
