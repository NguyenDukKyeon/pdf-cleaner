from pathlib import Path
import sys

ENGINE_DIR = Path(__file__).resolve().parents[2] / "backend" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
from tdm_cleaner.core import worker_pool


def test_auto_worker_selection_can_exceed_one(monkeypatch):
    monkeypatch.setattr(worker_pool.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(worker_pool, "memory_safe_worker_limit", lambda dpi: 6)
    assert worker_pool.auto_worker_count(0, page_count=20, dpi=240) == 6


def test_auto_worker_selection_respects_memory_page_and_request(monkeypatch):
    monkeypatch.setattr(worker_pool.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(worker_pool, "memory_safe_worker_limit", lambda dpi: 2)
    assert worker_pool.auto_worker_count(0, page_count=20, dpi=240) == 2
    assert worker_pool.auto_worker_count(8, page_count=1, dpi=240) == 1
