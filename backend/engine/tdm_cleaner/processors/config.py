from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_ENGINE_PATHS: dict[str, Path] = {
    "ebook": PROJECT_ROOT / "watermaker EBOOK.py",
    "ly": PROJECT_ROOT / "watermaker IPCLASS.py",
    "toan": PROJECT_ROOT / "watermaker TDM.py",
    "hoa": PROJECT_ROOT / "watermaker TYHH.py",
}


@dataclass(slots=True)
class ProcessorConfig:
    """Typed wrapper around one mode's configuration dictionary."""

    mode: str
    params: dict[str, Any] = field(default_factory=dict)
    engine_paths: dict[str, str | Path] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def engine_path(self, key: str) -> Path:
        raw = self.engine_paths.get(key) or DEFAULT_ENGINE_PATHS[key]
        return Path(raw)
