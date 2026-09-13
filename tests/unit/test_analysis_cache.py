from __future__ import annotations

import os
from pathlib import Path
import fitz
import pytest

from backend.engine.analyzer.models import DocumentKind, DocumentProfile
from backend.engine.pipeline_v2.analysis_cache import (
    AnalysisCache,
    AnalysisFingerprint,
    fingerprint_pdf,
)
from backend.engine.pipeline_v2.analyze import (
    analyze_document,
    clear_analysis_cache,
)
from tests.fixtures_factory import make_vector_overlay_pdf


def test_fingerprint_pdf_computation(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    # Create a 200 KiB file
    content = b"PDF-START" + b"X" * (200 * 1024 - 16) + b"PDF-END\n"
    pdf_path.write_bytes(content)

    fp = fingerprint_pdf(pdf_path)

    assert fp.size == len(content)
    assert fp.mtime_ns == pdf_path.stat().st_mtime_ns
    assert isinstance(fp.sample_sha256, str)
    assert len(fp.sample_sha256) == 64


def test_fingerprint_pdf_small_file(tmp_path):
    pdf_path = tmp_path / "small.pdf"
    content = b"%PDF-1.4 small"
    pdf_path.write_bytes(content)

    fp = fingerprint_pdf(pdf_path)

    assert fp.size == len(content)
    assert fp.mtime_ns == pdf_path.stat().st_mtime_ns
    assert len(fp.sample_sha256) == 64


def test_analysis_cache_hit_on_identical_fingerprint():
    cache = AnalysisCache(max_entries=32)
    path = Path("/fake/test_doc.pdf")
    fp = AnalysisFingerprint(size=12345, mtime_ns=1000000000, sample_sha256="a" * 64)
    profile = DocumentProfile(page_count=3, kind=DocumentKind.VECTOR, confidence=0.98)

    cache.put(path, fp, profile)

    # Identical path and fingerprint -> hit
    cached = cache.get(path, fp)
    assert cached is profile


def test_analysis_cache_miss_on_mtime_change():
    cache = AnalysisCache(max_entries=32)
    path = Path("/fake/test_doc.pdf")
    fp = AnalysisFingerprint(size=12345, mtime_ns=1000000000, sample_sha256="a" * 64)
    profile = DocumentProfile(page_count=3, kind=DocumentKind.VECTOR, confidence=0.98)

    cache.put(path, fp, profile)

    # Same size and content hash, but different mtime_ns -> miss
    fp_modified_mtime = AnalysisFingerprint(size=12345, mtime_ns=1000000999, sample_sha256="a" * 64)
    assert cache.get(path, fp_modified_mtime) is None


def test_analysis_cache_miss_on_first_byte_change_with_unchanged_size(tmp_path):
    cache = AnalysisCache(max_entries=32)
    pdf_path = tmp_path / "large_sample.pdf"
    # 200 KiB file
    payload = bytearray(b"A" * (200 * 1024))
    pdf_path.write_bytes(payload)

    fp1 = fingerprint_pdf(pdf_path)
    profile = DocumentProfile(page_count=5, kind=DocumentKind.VECTOR, confidence=0.95)
    cache.put(pdf_path, fp1, profile)

    # Mutate byte 10 (within first 64 KiB) without changing size
    payload[10] = ord(b"B")
    pdf_path.write_bytes(payload)

    fp2 = fingerprint_pdf(pdf_path)
    assert fp2.size == fp1.size
    assert fp2.sample_sha256 != fp1.sample_sha256

    # Even with same mtime, hash difference must miss
    fp2_forced_same_mtime = AnalysisFingerprint(
        size=fp1.size, mtime_ns=fp1.mtime_ns, sample_sha256=fp2.sample_sha256
    )
    assert cache.get(pdf_path, fp2_forced_same_mtime) is None
    assert cache.get(pdf_path, fp2) is None


def test_analysis_cache_miss_on_last_byte_change_with_unchanged_size(tmp_path):
    cache = AnalysisCache(max_entries=32)
    pdf_path = tmp_path / "large_sample.pdf"
    # 200 KiB file
    payload = bytearray(b"A" * (200 * 1024))
    pdf_path.write_bytes(payload)

    fp1 = fingerprint_pdf(pdf_path)
    profile = DocumentProfile(page_count=5, kind=DocumentKind.VECTOR, confidence=0.95)
    cache.put(pdf_path, fp1, profile)

    # Mutate byte near the end (within last 64 KiB) without changing size
    payload[-10] = ord(b"Z")
    pdf_path.write_bytes(payload)

    fp2 = fingerprint_pdf(pdf_path)
    assert fp2.size == fp1.size
    assert fp2.sample_sha256 != fp1.sample_sha256

    # Cache miss
    assert cache.get(pdf_path, fp2) is None


def test_analysis_cache_lru_eviction_of_oldest_on_33rd_insert():
    cache = AnalysisCache(max_entries=32)

    # Insert 32 items
    paths = [Path(f"/fake/doc_{i}.pdf") for i in range(33)]
    fps = [
        AnalysisFingerprint(size=1000 + i, mtime_ns=1000000 + i, sample_sha256=f"{i:064x}")
        for i in range(33)
    ]
    profiles = [
        DocumentProfile(page_count=i + 1, kind=DocumentKind.VECTOR, confidence=0.9)
        for i in range(33)
    ]

    for i in range(32):
        cache.put(paths[i], fps[i], profiles[i])

    assert len(cache) == 32
    assert cache.get(paths[0], fps[0]) is profiles[0]

    # After getting paths[0], paths[0] is most recently used.
    # The oldest is now paths[1].
    # Inserting item 32 (the 33rd distinct item) should evict paths[1].
    cache.put(paths[32], fps[32], profiles[32])
    assert len(cache) == 32

    # paths[0] was refreshed, so it must still be present
    assert cache.get(paths[0], fps[0]) is profiles[0]
    # paths[1] was least recently used, so it must be evicted
    assert cache.get(paths[1], fps[1]) is None
    # paths[32] was just inserted, so it must be present
    assert cache.get(paths[32], fps[32]) is profiles[32]


def test_analysis_cache_clean_sequential_eviction():
    cache = AnalysisCache(max_entries=32)

    paths = [Path(f"/fake/seq_{i}.pdf") for i in range(33)]
    fps = [
        AnalysisFingerprint(size=2000 + i, mtime_ns=2000000 + i, sample_sha256=f"{i:064x}")
        for i in range(33)
    ]
    profiles = [
        DocumentProfile(page_count=i + 1, kind=DocumentKind.VECTOR, confidence=0.9)
        for i in range(33)
    ]

    # Insert 32 items sequentially with no gets
    for i in range(32):
        cache.put(paths[i], fps[i], profiles[i])

    assert len(cache) == 32

    # Insert 33rd item: entry 0 must be evicted
    cache.put(paths[32], fps[32], profiles[32])
    assert len(cache) == 32
    assert cache.get(paths[0], fps[0]) is None
    assert cache.get(paths[1], fps[1]) is profiles[1]
    assert cache.get(paths[32], fps[32]) is profiles[32]


def test_cached_values_must_be_document_profile_never_handles_or_raw_bytes(tmp_path):
    cache = AnalysisCache(max_entries=32)
    path = Path("/fake/doc.pdf")
    fp = AnalysisFingerprint(size=100, mtime_ns=100, sample_sha256="f" * 64)

    # Reject open fitz.Document
    test_pdf = tmp_path / "test.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(test_pdf))
    open_doc = fitz.open(str(test_pdf))
    try:
        with pytest.raises(TypeError, match="DocumentProfile"):
            cache.put(path, fp, open_doc)  # type: ignore[arg-type]
    finally:
        open_doc.close()

    # Reject raw bytes
    with pytest.raises(TypeError, match="DocumentProfile"):
        cache.put(path, fp, b"%PDF-1.4 raw bytes")  # type: ignore[arg-type]

    # Reject dict or None
    with pytest.raises(TypeError, match="DocumentProfile"):
        cache.put(path, fp, {"page_count": 1})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DocumentProfile"):
        cache.put(path, fp, None)  # type: ignore[arg-type]

    # Valid DocumentProfile is accepted
    profile = DocumentProfile(page_count=1, kind=DocumentKind.VECTOR, confidence=0.99)
    cache.put(path, fp, profile)
    cached_val = cache.get(path, fp)

    assert isinstance(cached_val, DocumentProfile)
    assert not isinstance(cached_val, fitz.Document)
    assert not isinstance(cached_val, (bytes, bytearray))


def test_analysis_cache_clear():
    cache = AnalysisCache(max_entries=32)
    path = Path("/fake/doc.pdf")
    fp = AnalysisFingerprint(size=100, mtime_ns=100, sample_sha256="c" * 64)
    profile = DocumentProfile(page_count=1, kind=DocumentKind.VECTOR, confidence=0.99)

    cache.put(path, fp, profile)
    assert len(cache) == 1
    cache.clear()
    assert len(cache) == 0
    assert cache.get(path, fp) is None


def test_pipeline_v2_analyze_document_uses_cache_and_clear(tmp_path, monkeypatch):
    import backend.engine.pipeline_v2.analyze as analyze_mod

    pdf_path = make_vector_overlay_pdf(tmp_path / "cached_doc.pdf", pages=5)
    clear_analysis_cache()

    original_analyze = analyze_mod._analyze_document
    call_count = 0

    def spy_analyze(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_analyze(*args, **kwargs)

    monkeypatch.setattr(analyze_mod, "_analyze_document", spy_analyze)

    # First call: cache miss -> calls analyzer
    prof1 = analyze_document(pdf_path)
    assert call_count == 1
    assert prof1.page_count == 5

    # Second call: cache hit -> does NOT call analyzer
    prof2 = analyze_document(pdf_path)
    assert call_count == 1
    assert prof2 is prof1

    # Clear cache -> next call misses and calls analyzer
    clear_analysis_cache()
    prof3 = analyze_document(pdf_path)
    assert call_count == 2
    assert prof3.page_count == 5
