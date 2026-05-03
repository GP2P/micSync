from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class EjectResult:
    ok: bool
    detail: str | None = None


def eject_volume(volume_root: Path) -> EjectResult:
    try:
        result = subprocess.run(
            ["diskutil", "eject", str(volume_root)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return EjectResult(ok=False, detail="diskutil is unavailable")
    detail = (result.stderr or result.stdout or "").strip() or None
    return EjectResult(
        ok=result.returncode == 0,
        detail=detail,
    )
