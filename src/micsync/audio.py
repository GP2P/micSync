from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil
import subprocess
import sys


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


def preserve_path_timestamps(*, source_path: Path, dest_path: Path) -> None:
    shutil.copystat(source_path, dest_path)

    source_birthtime = getattr(source_path.stat(), "st_birthtime", None)
    if sys.platform != "darwin" or source_birthtime is None:
        return

    subprocess.run(
        [
            "SetFile",
            "-d",
            datetime.fromtimestamp(source_birthtime).strftime("%m/%d/%Y %H:%M:%S"),
            str(dest_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def preserve_file_timestamps(*, source_path: Path, dest_path: Path) -> None:
    preserve_path_timestamps(source_path=source_path, dest_path=dest_path)


def materialize_derived_file(
    *,
    source_path: Path,
    dest_path: Path,
    strategy: str,
) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        return dest_path

    if strategy in {"auto", "clone_then_copy"} and sys.platform == "darwin":
        result = subprocess.run(
            ["cp", "-c", str(source_path), str(dest_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return dest_path

    shutil.copy2(source_path, dest_path)
    return dest_path
