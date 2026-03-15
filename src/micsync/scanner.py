from __future__ import annotations

from pathlib import Path


def should_include_file(
    *,
    path: Path,
    file_size_bytes: int,
    allow_extensions: set[str],
    max_file_size_mb: int | None,
) -> bool:
    if path.suffix.lower() not in {ext.lower() for ext in allow_extensions}:
        return False
    if max_file_size_mb is None:
        return True
    return file_size_bytes <= max_file_size_mb * 1024 * 1024
