from __future__ import annotations

"""
core_stream.py
==============

TYHH stream-only core.

File này giữ lại phần stream-safe đang hoạt động tốt của core.py,
nhưng loại bỏ toàn bộ nhánh hybrid / raster vision / inpaint.

Không có:
- detect_repeated_overlay_masks
- hybrid-vision
- raster plan
- cv2.inpaint
- clean full-page raster

Có:
- phân tích stream lặp/marker
- xóa stream SAFE bằng PyMuPDF
- lưu PDF an toàn qua file .tmp rồi os.replace
"""

import hashlib
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

try:
    import fitz
except Exception as e:
    raise SystemExit("Cài: pip install pymupdf\n" + str(e))


PRESETS = {
    "TaiLieuOnThi (khuyên dùng)": [
        "TAILIEUONTHI.NET",
        "TaiLieuOnThi.Net",
        "TaiLieuOnThi",
        "TAILIEUONTHI",
        "Tai Lieu On Thi",
        "Tài Liệu Ôn Thi Group",
    ],
    "Chỉ TAILIEUONTHI": [
        "TAILIEUONTHI",
        "TaiLieuOnThi",
        "Tai Lieu On Thi",
    ],
    "Trống": [],
}

REAL_CONTENT_MARKERS = [
    "Câu ",
    "BÀI",
    "VNA",
    "Mapstudy",
    "Thầy",
    "Học online",
    "---Hết---",
    "A.",
    "B.",
    "C.",
    "D.",
]

URL_HINTS = ["http://", "https://", ".com", ".net", ".org", "www."]


@dataclass
class Candidate:
    engine: str
    file: str
    page: str
    xref: int
    score: int
    safe: bool
    markers: str
    length: int
    repeat_count: int
    note: str


@dataclass
class StreamEval:
    score: int
    safe: bool
    note: str
    matched_markers: list[str]
    content_hits: list[str]


class CancelledByUser(Exception):
    """Raised internally when user cancels a scan/clean job."""


def _check_cancel(should_cancel: Callable[[], bool] | None) -> None:
    if callable(should_cancel) and should_cancel():
        raise CancelledByUser("Đã hủy theo yêu cầu người dùng.")


def collect_pdf_files(source_path: str | Path, recursive: bool = False) -> list[Path]:
    p = Path(source_path)
    if p.is_file() and p.suffix.lower() == ".pdf":
        return [p]
    if p.is_dir():
        return sorted(p.glob("**/*.pdf" if recursive else "*.pdf"))
    return []


def parse_markers(text: str) -> list[str]:
    return [x.strip() for x in str(text).splitlines() if x.strip() and not x.strip().startswith("#")]


def try_encode_ascii(s: str) -> bytes:
    try:
        return str(s).encode("ascii", errors="ignore")
    except Exception:
        return b""


def decode_probe(b: bytes) -> str:
    return b.decode("latin1", errors="ignore")


def upper_ratio(t: str) -> float:
    letters = [c for c in t if c.isalpha()]
    return 0.0 if not letters else sum(c.isupper() for c in letters) / len(letters)


def resolve_pdf_key(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(Path(path))


def collect_stream_cache(doc: fitz.Document) -> tuple[list[tuple[int, int]], Counter, dict[int, bytes], dict[int, str]]:
    """Collect page content streams once.

    The previous implementation read every stream once to build the repeat map and
    then read the same streams again during analyze/remove.  This helper keeps the
    same repeat-count logic but reuses the bytes already loaded from PyMuPDF.
    """
    page_xrefs: list[tuple[int, int]] = []
    counter: Counter = Counter()
    stream_cache: dict[int, bytes] = {}
    hash_cache: dict[int, str] = {}
    for pno, page in enumerate(doc, start=1):
        for xref in page.get_contents() or []:
            try:
                stream = stream_cache.get(xref)
                if stream is None:
                    stream = doc.xref_stream(xref)
                    stream_cache[xref] = stream
                    hash_cache[xref] = hashlib.sha1(stream).hexdigest()
                digest = hash_cache[xref]
            except Exception:
                continue
            page_xrefs.append((pno, xref))
            counter[digest] += 1
    return page_xrefs, counter, stream_cache, hash_cache


def build_stream_repeat_map(doc: fitz.Document) -> Counter:
    _page_xrefs, counter, _stream_cache, _hash_cache = collect_stream_cache(doc)
    return counter


def marker_matches(stream_bytes: bytes, markers: list[str]) -> list[str]:
    matched: list[str] = []
    low = stream_bytes.lower()
    for marker in markers:
        b = try_encode_ascii(marker)
        if not b:
            continue
        hex_bytes = b.hex().encode("ascii")
        if b in stream_bytes or b.lower() in low or hex_bytes in low:
            matched.append(marker)
    return matched


def content_hits(text: str) -> list[str]:
    return [x for x in REAL_CONTENT_MARKERS if x in text]


def evaluate_stream(
    stream_bytes: bytes,
    markers: list[str],
    repeat_count: int = 1,
    allow_auto_detect: bool = False,
) -> StreamEval:
    """
    Chấm điểm stream PDF.

    SAFE khi:
    - khớp marker watermark,
    - không dính nội dung thật,
    - stream không quá dài.
    """
    probe = decode_probe(stream_bytes)
    matched = marker_matches(stream_bytes, markers)
    hits = content_hits(probe)

    score = 0
    reasons: list[str] = []
    length = len(stream_bytes)

    if matched:
        score += 65
        reasons.append("khớp marker: " + ", ".join(matched[:3]))

    if length <= 1800:
        score += 18
        reasons.append("stream ngắn")
    elif length <= 5000:
        score += 8
        reasons.append("stream vừa")
    elif length > 12000:
        score -= 30
        reasons.append("stream dài")

    if repeat_count >= 2:
        score += 18
        reasons.append(f"lặp {repeat_count} lần")
    if repeat_count >= 4:
        score += 10

    if any(h in probe for h in URL_HINTS):
        score += 10
        reasons.append("có URL/domain")

    if upper_ratio(probe) >= 0.70:
        score += 6
        reasons.append("chữ hoa nhiều")

    if hits:
        score -= 90
        reasons.append("dính nội dung thật: " + ", ".join(hits[:3]))

    safe = bool(matched and not hits and length <= 5000)

    if allow_auto_detect and (not safe) and (not matched) and (not hits):
        if repeat_count >= 2 and length <= 2500 and (
            any(h in probe for h in URL_HINTS) or upper_ratio(probe) >= 0.75
        ):
            score += 25
            reasons.append("auto-detect stream")
            safe = score >= 70

    return StreamEval(
        score=score,
        safe=safe,
        note="; ".join(reasons) if reasons else "không có dấu hiệu rõ",
        matched_markers=matched,
        content_hits=hits,
    )


def analyze_stream_candidates(
    doc: fitz.Document,
    input_pdf: str | Path,
    markers: list[str],
    allow_auto: bool = False,
    log: Callable[[str], None] | None = None,
) -> list[Candidate]:
    rows: list[Candidate] = []
    page_xrefs, repeat_map, stream_cache, hash_cache = collect_stream_cache(doc)

    for pno, xref in page_xrefs:
        stream = stream_cache[xref]
        repeat_count = repeat_map[hash_cache[xref]]
        ev = evaluate_stream(stream, markers, repeat_count, allow_auto)

        if ev.safe or ev.score >= 35:
            rows.append(
                Candidate(
                    engine="stream-safe",
                    file=str(input_pdf),
                    page=str(pno),
                    xref=xref,
                    score=ev.score,
                    safe=ev.safe,
                    markers=", ".join(ev.matched_markers) if ev.matched_markers else "",
                    length=len(stream),
                    repeat_count=repeat_count,
                    note=ev.note,
                )
            )
            if log and ev.safe:
                log(f"  - Stream SAFE: trang {pno}, xref={xref}, score={ev.score}")

    rows.sort(key=lambda x: (not x.safe, -x.score, x.page, x.xref))
    return rows


def remove_safe_streams_inplace(
    doc: fitz.Document,
    markers: list[str],
    allow_auto: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    page_xrefs, repeat_map, stream_cache, hash_cache = collect_stream_cache(doc)
    removed = 0
    changed_pages: set[int] = set()
    skipped_streams: list[tuple[int, int, int, str]] = []
    removed_xrefs: set[int] = set()

    for pno, xref in page_xrefs:
        if xref in removed_xrefs:
            # Same xref may be referenced by multiple pages.  The old loop updated
            # it once, then later reads saw an empty stream.  Skipping duplicates
            # preserves that behavior while avoiding redundant PyMuPDF calls.
            continue
        stream = stream_cache[xref]
        repeat_count = repeat_map[hash_cache[xref]]
        ev = evaluate_stream(stream, markers, repeat_count, allow_auto)

        if ev.safe:
            doc.update_stream(xref, b"")
            removed_xrefs.add(xref)
            removed += 1
            changed_pages.add(pno)
            if log:
                log(f"  - Đã gỡ stream SAFE: trang {pno}, xref={xref}, score={ev.score}")
        elif ev.score >= 35:
            skipped_streams.append((pno, xref, ev.score, ev.note))

    return {
        "removed_streams": removed,
        "changed_pages": changed_pages,
        "skipped_streams": skipped_streams,
    }


def clean_pdf_stream_safe(
    input_pdf: str | Path,
    output_pdf: str | Path,
    markers: list[str],
    allow_stream_auto: bool = False,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Xóa watermark bằng stream-safe, không raster, không inpaint.

    Ghi ra file .tmp trước. Nếu output_pdf trùng input_pdf thì đóng document
    đang đọc trước rồi mới os.replace, tránh lỗi PermissionError trên Windows.
    """
    input_pdf = Path(input_pdf).expanduser().resolve()
    output_pdf = Path(output_pdf).expanduser().resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=output_pdf.stem + "_", suffix=".tmp.pdf", dir=str(output_pdf.parent))
    os.close(fd)
    temp_path = Path(tmp_name)
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass

    same_path = input_pdf == output_pdf
    copy_after_close = False
    replace_after_close = False
    removed_streams = 0
    changed_pages_count = 0
    skipped_streams_count = 0

    doc = fitz.open(str(input_pdf))
    try:
        _check_cancel(should_cancel)
        if doc.is_encrypted:
            raise ValueError(f"File bị mã hóa, không xử lý được: {input_pdf}")

        result = remove_safe_streams_inplace(
            doc,
            markers=markers,
            allow_auto=allow_stream_auto,
            log=log,
        )

        _check_cancel(should_cancel)

        removed_streams = int(result["removed_streams"])
        changed_pages_count = len(result["changed_pages"])
        skipped_streams_count = len(result["skipped_streams"])

        # Nếu không có stream nào bị xóa, output phải giống input. Khi ghi đè
        # chính file gốc và không có thay đổi thì không cần ghi lại.
        if removed_streams <= 0:
            copy_after_close = not same_path
        else:
            doc.save(str(temp_path), garbage=4, deflate=True)
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                raise RuntimeError(f"File tạm rỗng sau khi save: {temp_path}")
            replace_after_close = True
    finally:
        doc.close()

    try:
        if copy_after_close:
            shutil.copy2(str(input_pdf), str(output_pdf))
        elif replace_after_close:
            os.replace(str(temp_path), str(output_pdf))

        return {
            "removed_streams": removed_streams,
            "changed_pages": changed_pages_count,
            "skipped_streams": skipped_streams_count,
            "raster_pages": 0,
            "preview_dir": "",
            "saved_to": str(output_pdf),
        }
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        except Exception:
            pass

def analyze_pdf_hybrid(
    input_pdf: str | Path,
    markers: list[str],
    allow_stream_auto: bool = False,
    enable_raster: bool = False,
    raster_dpi: int = 120,
    vision_strictness: float = 1.0,
    log: Callable[[str], None] | None = None,
    return_ui_preview: bool = False,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
):
    """
    Compatibility wrapper cho GUI cũ.

    Dù tên còn là analyze_pdf_hybrid, bản stream-only này bỏ qua enable_raster
    và chỉ phân tích stream-safe.
    """
    doc = fitz.open(str(input_pdf))
    try:
        _check_cancel(should_cancel)
        rows = analyze_stream_candidates(doc, input_pdf, markers, allow_stream_auto, log)
        return (rows, {}) if return_ui_preview else rows
    finally:
        doc.close()


def clean_pdf_hybrid(
    input_pdf: str | Path,
    output_pdf: str | Path,
    markers: list[str],
    allow_stream_auto: bool = False,
    enable_raster: bool = False,
    raster_dpi: int = 170,
    vision_strictness: float = 1.0,
    save_preview: bool = False,
    user_corrections: dict | None = None,
    log: Callable[[str], None] | None = None,
    allowed_vision_pages: set | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Compatibility wrapper cho tên hàm cũ.

    Quan trọng:
    - Không dùng enable_raster.
    - Không gọi cv2.inpaint.
    - Không chạy hybrid vision.
    """
    if log and enable_raster:
        log("  - TYHH stream-only: đã bỏ qua hybrid/raster/inpaint theo cấu hình Step 3.1.")
    return clean_pdf_stream_safe(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        markers=markers,
        allow_stream_auto=allow_stream_auto,
        log=log,
        should_cancel=should_cancel,
    )
