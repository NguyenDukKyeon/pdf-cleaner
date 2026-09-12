from __future__ import annotations

from pathlib import Path
from typing import Any
import importlib.util
import sys

_ENGINE_CACHE: dict[Path, Any] = {}


def load_engine(path: str | Path, module_hint: str) -> Any:
    """Load a legacy engine file whose filename may contain spaces."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy engine: {path}")
    if path in _ENGINE_CACHE:
        return _ENGINE_CACHE[path]

    module_name = f"_watermark_engine_{module_hint}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Không tạo được import spec cho engine: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            f"Không import được engine {path.name}. Thiếu module: {exc.name}."
        ) from exc

    _ENGINE_CACHE[path] = module
    return module
