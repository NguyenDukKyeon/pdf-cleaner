"""Compatibility wrapper for the refactored config package."""

from tdm_cleaner.config.manager import ConfigManager, deep_merge
from tdm_cleaner.config.defaults import DEFAULT_CONFIG, QUALITY_PRESETS

__all__ = ["ConfigManager", "deep_merge", "DEFAULT_CONFIG", "QUALITY_PRESETS"]
