from __future__ import annotations

from pathlib import Path
import subprocess


def eject_volume(volume_root: Path) -> bool:
    result = subprocess.run(
        ["diskutil", "eject", str(volume_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
