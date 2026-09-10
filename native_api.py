"""Direct JavaScript <-> Python bridge for the native PDF_Cleaner window.

Desktop-only build: no FastAPI, no HTTP port, no browser backend.  The service
module is imported lazily so the window can paint before the PDF engine loads.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from typing import Any, Callable


class NativeApi:
    def __init__(self) -> None:
        self._service_module: Any = None
        self._service_lock = threading.Lock()
        self._window: Any = None

    def bind_window(self, window: Any) -> None:
        self._window = window

    def _service(self) -> Any:
        if self._service_module is not None:
            return self._service_module
        with self._service_lock:
            if self._service_module is None:
                self._service_module = importlib.import_module("backend.service")
        return self._service_module

    def _safe(self, callback: Callable[[], Any]) -> Any:
        try:
            return callback()
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    def _require_window(self) -> Any:
        if self._window is None:
            raise RuntimeError("Cửa sổ ứng dụng chưa sẵn sàng.")
        return self._window

    @staticmethod
    def _dialog_kind(webview_module: Any, modern_name: str, legacy_name: str) -> Any:
        file_dialog = getattr(webview_module, "FileDialog", None)
        if file_dialog is not None and hasattr(file_dialog, modern_name):
            return getattr(file_dialog, modern_name)
        value = getattr(webview_module, legacy_name, None)
        if value is None:
            raise RuntimeError("Phiên bản pywebview không hỗ trợ hộp thoại file native.")
        return value

    def ping(self) -> dict[str, Any]:
        return {"ok": True, "mode": "native", "server": False}

    def get_config(self) -> dict[str, Any]:
        return self._safe(lambda: self._service().get_config())

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._safe(lambda: self._service().save_settings(payload))

    def reset_settings(self) -> dict[str, Any]:
        return self._safe(lambda: self._service().reset_settings())

    def choose_local_files(self) -> dict[str, Any]:
        def choose() -> dict[str, Any]:
            import webview

            window = self._require_window()
            kind = self._dialog_kind(webview, "OPEN", "OPEN_DIALOG")
            selected = window.create_file_dialog(
                kind,
                allow_multiple=True,
                file_types=("PDF files (*.pdf)",),
            ) or ()
            service = self._service()
            files = []
            for raw in selected:
                path = service.validate_local_pdf_path(raw)
                files.append(service.local_pdf_info(path))
            return {"files": files}

        return self._safe(choose)

    def choose_output_dir(self) -> dict[str, Any]:
        def choose() -> dict[str, Any]:
            import webview

            window = self._require_window()
            kind = self._dialog_kind(webview, "FOLDER", "FOLDER_DIALOG")
            selected = window.create_file_dialog(kind) or ()
            if not selected:
                return {"path": ""}
            raw = selected[0] if isinstance(selected, (tuple, list)) else selected
            folder = Path(str(raw)).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            return {"path": str(folder)}

        return self._safe(choose)

    def start_process(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._safe(lambda: self._service().start_process_local(payload))

    def poll_job(self, job_id: str, after_event_id: int = 0) -> dict[str, Any]:
        return self._safe(lambda: self._service().poll_job(job_id, after_event_id))

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._safe(lambda: self._service().cancel_job(job_id))

    def open_latest_file(self, job_id: str) -> dict[str, Any]:
        return self._safe(lambda: self._service().open_latest_file(job_id))

    def open_latest_folder(self, job_id: str) -> dict[str, Any]:
        return self._safe(lambda: self._service().open_latest_folder(job_id))

    def open_log(self) -> dict[str, Any]:
        return self._safe(lambda: self._service().open_log())

    def open_tool_dir(self) -> dict[str, Any]:
        return self._safe(lambda: self._service().open_tool_dir())
