from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading

from typing import Any

from backend.engine.analyzer.models import DocumentProfile


def freeze_analysis_options(
    options: tuple[tuple[str, Any], ...] | dict[str, Any] | None = None,
) -> tuple[tuple[str, Any], ...]:
    if not options:
        return ()
    if isinstance(options, dict):
        items = []
        for k, v in options.items():
            items.append((str(k), _freeze_val(v)))
        return tuple(sorted(items, key=lambda x: x[0]))
    if isinstance(options, (tuple, list)):
        items = []
        for item in options:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                k, v = item
                items.append((str(k), _freeze_val(v)))
            else:
                items.append((str(item), True))
        return tuple(sorted(items, key=lambda x: x[0]))
    return ()


def _freeze_val(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return tuple(_freeze_val(x) for x in v)
    if isinstance(v, set):
        try:
            return tuple(sorted(_freeze_val(x) for x in v))
        except TypeError:
            return tuple(sorted(str(x) for x in v))
    if isinstance(v, dict):
        return tuple(sorted((str(k), _freeze_val(val)) for k, val in v.items()))
    try:
        hash(v)
        return v
    except TypeError:
        return str(v)


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


CacheKey = tuple[Path, AnalysisFingerprint, tuple[tuple[str, Any], ...]]


class AnalysisCache:
    def __init__(self, max_entries: int = 32) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._entries: OrderedDict[CacheKey, DocumentProfile] = OrderedDict()

    def get(
        self,
        path: Path | str,
        fingerprint: AnalysisFingerprint,
        options: tuple[tuple[str, Any], ...] | dict[str, Any] | None = None,
    ) -> DocumentProfile | None:
        norm_options = freeze_analysis_options(options)
        key: CacheKey = (Path(path).resolve(), fingerprint, norm_options)
        with self._lock:
            profile = self._entries.get(key)
            if profile is not None:
                self._entries.move_to_end(key)
                return profile
            return None

    def put(
        self,
        path: Path | str,
        fingerprint: AnalysisFingerprint,
        profile: DocumentProfile,
        options: tuple[tuple[str, Any], ...] | dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(profile, DocumentProfile):
            raise TypeError(f"Expected DocumentProfile, got {type(profile).__name__}")
        norm_options = freeze_analysis_options(options)
        key: CacheKey = (Path(path).resolve(), fingerprint, norm_options)
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

