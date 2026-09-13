from __future__ import annotations

from native_api import NativeApi


class FakeService:
    def __init__(self) -> None:
        self.started_payload: dict | None = None
        self.cancelled_job: str | None = None

    def start_process_local(self, payload: dict) -> dict:
        self.started_payload = dict(payload)
        return {"job_id": "job-123", "status": "queued"}

    def poll_job(self, job_id: str, after_event_id: int = 0) -> dict:
        return {
            "events": [
                {
                    "id": 1,
                    "type": "stage",
                    "message": "ANALYZING",
                    "document_kind": "raster",
                    "confidence": 0.97,
                }
            ],
            "last_event_id": 1,
            "status": "running",
            "stage": "ANALYZING",
            "percent": 8.0,
            "qc_report": None,
            "terminal": False,
        }

    def cancel_job(self, job_id: str) -> dict:
        self.cancelled_job = job_id
        return {"ok": True, "status": "running"}


def make_api() -> tuple[NativeApi, FakeService]:
    api = NativeApi()
    service = FakeService()
    api._service_module = service
    return api, service


def test_start_process_defaults_to_auto_v2_and_auto_content_profile() -> None:
    api, service = make_api()

    result = api.start_process({"paths": ["sample.pdf"], "preset": "balanced"})

    assert result["job_id"] == "job-123"
    assert service.started_payload is not None
    assert service.started_payload["mode"] == "auto"
    assert service.started_payload["content_profile"] == "auto"
    assert service.started_payload["engine_preference"] == "auto_smart"
    assert service.started_payload["footer_cleanup"] == "auto"


def test_start_process_preserves_explicit_engine_and_footer_preferences() -> None:
    api, service = make_api()

    result = api.start_process(
        {
            "paths": ["sample.pdf"],
            "engine_preference": "raster_clean",
            "footer_cleanup": "deep",
        }
    )

    assert result["job_id"] == "job-123"
    assert service.started_payload is not None
    assert service.started_payload["engine_preference"] == "raster_clean"
    assert service.started_payload["footer_cleanup"] == "deep"


def test_native_job_bridge_preserves_stage_report_payload_and_cancel() -> None:
    api, service = make_api()

    poll = api.poll_job("job-123", 0)
    cancel = api.cancel_job("job-123")

    assert poll["stage"] == "ANALYZING"
    assert poll["events"][0]["document_kind"] == "raster"
    assert "qc_report" in poll
    assert cancel["ok"] is True
    assert service.cancelled_job == "job-123"


def test_service_start_process_local_rejects_invalid_engine_preference(tmp_path) -> None:
    import pytest
    from backend.service import start_process_local

    pdf = tmp_path / "dummy.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    with pytest.raises(ValueError, match="engine_preference"):
        start_process_local({"paths": [str(pdf)], "engine_preference": "turbo_clean"})


def test_service_start_process_local_rejects_invalid_footer_cleanup(tmp_path) -> None:
    import pytest
    from backend.service import start_process_local

    pdf = tmp_path / "dummy.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    with pytest.raises(ValueError, match="footer_cleanup"):
        start_process_local({"paths": [str(pdf)], "footer_cleanup": "extreme"})

