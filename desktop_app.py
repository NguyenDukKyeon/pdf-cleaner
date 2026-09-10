"""Native PDF_Cleaner desktop shell.

The UI is loaded from local files into Edge WebView2. JavaScript talks directly
to Python through pywebview's JS API; no local web server is started.
"""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import webview

from native_api import NativeApi


APP_DIR = Path(__file__).resolve().parent
INDEX_FILE = APP_DIR / "frontend" / "index.html"
STORAGE_DIR = Path(os.environ.get("LOCALAPPDATA", APP_DIR)) / "PDF_Cleaner" / "WebView"


def main() -> None:
    if not INDEX_FILE.exists():
        raise FileNotFoundError(f"Không tìm thấy giao diện: {INDEX_FILE}")

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    api = NativeApi()
    window = webview.create_window(
        "PDF_Cleaner",
        INDEX_FILE.as_uri(),
        js_api=api,
        width=1100,
        height=760,
        min_size=(800, 600),
    )
    api.bind_window(window)
    webview.start(
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(STORAGE_DIR),
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
