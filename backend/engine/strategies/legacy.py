from __future__ import annotations
from pathlib import Path
from typing import Callable
import sys
from backend.engine.router.models import ProcessingPlan

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
from tdm_cleaner.config.manager import ConfigManager
from tdm_cleaner.core.pipeline import process_pdf_optimized
from .base import StrategyResult

_PROFILE_TO_MODE = {'auto':'toan','math':'toan','physics':'ly','chemistry':'hoa','ebook':'ebook'}

class LegacyStrategy:
    def __init__(self, *, config_path: Path | None = None):
        self.config_path = config_path or Path(__file__).resolve().parents[1] / 'config.json'
    def execute(self, input_pdf: Path, output_pdf: Path, plan: ProcessingPlan, *, workers: int = 0, dpi: int = 240, output_dpi: int = 240, quality: int = 92, log: Callable[[str], None] | None = None, progress=None, should_cancel=None) -> StrategyResult:
        manager = ConfigManager(self.config_path); manager.load()
        mode = _PROFILE_TO_MODE.get(plan.content_profile, 'toan')
        mode_config = dict(manager.get_mode_config(mode)); mode_config.pop('display_name', None)
        if mode in {'toan','tdm'}: mode_config['graph_safe_paper_mode'] = True
        report = process_pdf_optimized(input_pdf=Path(input_pdf), output_pdf=Path(output_pdf), mode=mode, mode_config=mode_config, dpi=int(dpi), output_dpi=int(output_dpi), quality=int(quality), workers=int(workers or 0), log=log, progress=progress, should_cancel=should_cancel)
        return StrategyResult(changed_pages=int(report.get('pages') or 0), rasterized_pages=len(report.get('full_raster_pages') or []), saved_to=str(output_pdf))
