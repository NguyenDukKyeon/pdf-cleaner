
from __future__ import annotations

"""
Headless compatibility shim for the unified PDF Scan Cleaner.

The old watermaker TYHH.py file contained only a standalone Tkinter GUI wrapper.
main.py processes Hóa/TYHH through core_stream.clean_pdf_stream_safe directly, so
keeping the old GUI here only slows imports and can confuse users.  The stream
algorithm remains in core_stream.py/core.py and is not changed by this shim.
"""

from core_stream import (  # noqa: F401
    PRESETS,
    Candidate,
    StreamEval,
    CancelledByUser,
    clean_pdf_stream_safe,
    collect_pdf_files,
    parse_markers,
)


def main() -> None:
    raise SystemExit("Standalone GUI của watermaker TYHH.py đã được bỏ. Hãy chạy python main.py.")


if __name__ == "__main__":
    main()
