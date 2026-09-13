from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading

from backend.engine.analyzer.models import DocumentProfile


@dataclass(frozen=True, slots=True)
class AnalysisFingerprint:
    size: int
    mtime_ns: int
    sample_sha256: str

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError("size must be non-negative")
        if self.mtime_ns < 0:
            raise ValueError("mtime_ns must be non-negative")
        if not self.sample_sha256:
            raise ValueError("sample_sha256 must not be empty")


def fingerprint_pdf(path: str | Path, sample_bytes: int = 65536) -> AnalysisFingerprint:
    if sample_bytes <= 0:
        raise ValueError("sample_bytes must be positive")

    p = Path(path).resolve()
    stat_res = p.stat()
    size = stat_res.st_size
    mtime_ns = stat_res.st_mtime_ns

    hasher = hashlib.sha256()
    hasher.update(f"size:{size}:".encode("ascii"))

    with p.open("rb") as f:
        if size <= sample_bytes * 2:
            hasher.update(f.read())
        else:
            hasher.update(f.read(sample_bytes))
            f.seek(size - sample_bytes)
            hasher.update(f.read(sample_bytes))

    return AnalysisFingerprint(
        size=size,
        mtime_ns=mtime_ns,
        sample_sha256=hasher.hexdigest(),
    )


class AnalysisCache:
    def __init__(self, max_entries: int = 32) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[Path, AnalysisFingerprint], DocumentProfile] = OrderedDict()

    def get(self, path: Path | str, fingerprint: AnalysisFingerprint) -> DocumentProfile | None:
        key = (Path(path).resolve(), fingerprint)
        with self._lock:
            profile = self._entries.get(key)
            if profile is not None:
                self._entries.move_to_end(key)
                return profile
            return None

    def put(self, path: Path | str, fingerprint: AnalysisFingerprint, profile: DocumentProfile) -> None:
        if not isinstance(profile, DocumentProfile):
            raise TypeError(f"Expected DocumentProfile, got {type(profile).__name__}")
        key = (Path(path).resolve(), fingerprint)
        with self._lock:
            self._entries[key] = profile
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
