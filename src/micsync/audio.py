from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import subprocess


def derive_end_time(start_at_iso: str, duration_ms: int | None) -> str | None:
    if duration_ms is None:
        return None
    start_at = datetime.fromisoformat(start_at_iso)
    end_at = start_at + timedelta(milliseconds=duration_ms)
    return end_at.isoformat(timespec="seconds")


def read_duration_ms(path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["afinfo", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("estimated duration:"):
            _, _, value = line.partition(":")
            duration_text = value.strip().split()[0]
            try:
                return int(float(duration_text) * 1000)
            except ValueError:
                return None
    return None
