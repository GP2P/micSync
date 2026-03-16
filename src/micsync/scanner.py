from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
import os

from micsync.parser import parse_recording_name


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


@dataclass(frozen=True)
class CandidateFile:
    volume_label: str
    volume_root: Path
    source_path: Path
    source_parent_folder: str
    file_size_bytes: int


def scan_candidates(
    *,
    allow_extensions: set[str],
    max_file_size_mb: int | None,
    volumes_root: Path = Path("/Volumes"),
    exclude_volume_labels: set[str] | None = None,
    include_volume_roots: list[Path] | None = None,
) -> list[CandidateFile]:
    if include_volume_roots is None and not volumes_root.exists():
        return []

    excluded = exclude_volume_labels or set()
    candidates: list[CandidateFile] = []
    if include_volume_roots is None:
        volume_roots = sorted(path for path in volumes_root.iterdir() if path.is_dir())
    else:
        volume_roots = sorted({path for path in include_volume_roots if path.is_dir()})

    for volume_root in volume_roots:
        if volume_root.name in excluded:
            continue
        for root, _, files in os.walk(volume_root, topdown=True, onerror=lambda _: None):
            root_path = Path(root)
            for filename in files:
                path = root_path / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if not path.is_file():
                    continue
                if not should_include_file(
                    path=path,
                    file_size_bytes=stat.st_size,
                    allow_extensions=allow_extensions,
                    max_file_size_mb=max_file_size_mb,
                ):
                    continue
                try:
                    parse_recording_name(path.name)
                except ValueError:
                    continue
                candidates.append(
                    CandidateFile(
                        volume_label=volume_root.name,
                        volume_root=volume_root,
                        source_path=path,
                        source_parent_folder=path.parent.name,
                        file_size_bytes=stat.st_size,
                    )
                )
    return candidates
