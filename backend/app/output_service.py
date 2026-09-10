from __future__ import annotations
import os, shutil, uuid
from pathlib import Path

def make_staging_path(final_path: str | Path) -> Path:
    final=Path(final_path).expanduser().resolve(); return final.with_name(f"{final.stem}.v2stage_{uuid.uuid4().hex[:8]}.pdf")
def cleanup_path(path: str | Path | None) -> None:
    if path is None: return
    try: Path(path).unlink(missing_ok=True)
    except Exception: pass
def atomic_promote(staging: str | Path, final: str | Path, *, backup_existing: bool=False) -> Path | None:
    staging=Path(staging).expanduser().resolve(); final=Path(final).expanduser().resolve()
    if not staging.exists(): raise FileNotFoundError(staging)
    final.parent.mkdir(parents=True, exist_ok=True); backup=None
    if final.exists() and backup_existing:
        backup=final.with_name(f"{final.stem}_backup{final.suffix}"); counter=2
        while backup.exists(): backup=final.with_name(f"{final.stem}_backup_{counter}{final.suffix}"); counter += 1
        shutil.copy2(final,backup)
    os.replace(str(staging),str(final)); return backup
