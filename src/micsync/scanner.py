from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

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
) -> list[CandidateFile]:
    volumes_root = Path("/Volumes")
    if not volumes_root.exists():
        return []

    candidates: list[CandidateFile] = []
    for volume_root in sorted(path for path in volumes_root.iterdir() if path.is_dir()):
        for path in volume_root.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
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
