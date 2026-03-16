from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
from typing import Callable

from micsync.audio import derive_end_time, read_duration_ms
from micsync.catalog import Catalog
from micsync.logging_utils import append_run_log
from micsync.parser import ParsedRecordingName, parse_physical_mic_id, parse_recording_name


@dataclass(frozen=True)
class ImportOutcome:
    final_path: Path
    checksum: str
    size_bytes: int
    status: str
    recording_id: int
    file_id: int


def compute_file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_destination_path(
    *,
    recordings_root: Path,
    relative_dir: Path,
    dest_name: str,
    incoming_checksum: str,
    existing_checksum_lookup: Callable[[Path], str],
) -> Path:
    target_dir = recordings_root / relative_dir
    candidate = target_dir / dest_name
    if not candidate.exists():
        return candidate
    if existing_checksum_lookup(candidate) == incoming_checksum:
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        dup_candidate = target_dir / f"{stem}_dup{counter}{suffix}"
        if not dup_candidate.exists():
            return dup_candidate
        counter += 1


def _recordings_relative_dir(start_at: datetime) -> Path:
    return Path("audio") / start_at.strftime("%Y") / start_at.strftime("%m") / start_at.strftime("%d")


def _copy_with_checksum(source_path: Path, tmp_path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open("rb") as src, tmp_path.open("wb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    return digest.hexdigest(), size_bytes


def import_recording(
    *,
    source_path: Path,
    source_mount_path: Path,
    source_parent_folder: str,
    volume_label: str | None,
    recordings_root: Path,
    tmp_root: Path,
    catalog: Catalog,
    log_path: Path,
    run_id: str,
    audio_subdir: str | None = None,
) -> ImportOutcome:
    parsed: ParsedRecordingName = parse_recording_name(source_path.name)
    recording_start_at = parsed.start_at.isoformat(timespec="seconds")
    duration_ms = read_duration_ms(source_path)
    recording_end_at = derive_end_time(recording_start_at, duration_ms)
    physical_mic_id = parse_physical_mic_id(volume_label)
    recording_id = catalog.upsert_recording(
        recording_group_key=parsed.recording_group_key,
        recording_start_at=recording_start_at,
        recording_end_at=recording_end_at,
        tx_slot=parsed.tx_slot,
        mic_sequence=parsed.mic_sequence,
        physical_mic_id=physical_mic_id,
    )

    tmp_path = tmp_root / f"{parsed.recording_group_key}{source_path.suffix}.tmp"
    checksum, size_bytes = _copy_with_checksum(source_path, tmp_path)
    attempted_at = datetime.now().isoformat(timespec="seconds")
    relative_dir = _recordings_relative_dir(parsed.start_at)
    if audio_subdir:
        relative_dir = Path("audio") / audio_subdir / relative_dir.relative_to("audio")
    final_path = plan_destination_path(
        recordings_root=recordings_root,
        relative_dir=relative_dir,
        dest_name=parsed.dest_name,
        incoming_checksum=checksum,
        existing_checksum_lookup=compute_file_checksum,
    )

    status = "imported"
    if final_path.exists():
        status = "duplicate"
        tmp_path.unlink(missing_ok=True)
    else:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.replace(final_path)

    file_id = catalog.insert_recording_file(
        recording_id=recording_id,
        recording_group_key=parsed.recording_group_key,
        run_id=run_id,
        source_volume_label=volume_label,
        source_volume_identifier=volume_label,
        source_mount_path=str(source_mount_path),
        source_parent_folder=source_parent_folder,
        source_filename=source_path.name,
        source_relative_path=str(Path(source_parent_folder) / source_path.name),
        source_size_bytes=size_bytes,
        source_checksum=checksum,
        recording_start_at=recording_start_at,
        recording_end_at=recording_end_at,
        tx_slot=parsed.tx_slot,
        mic_sequence=parsed.mic_sequence,
        variant=parsed.variant,
        content_role=parsed.variant,
        duration_ms=duration_ms,
        physical_mic_id=physical_mic_id,
        dest_relative_path=str(final_path.relative_to(recordings_root)),
        dest_size_bytes=size_bytes,
        import_status=status,
        first_seen_at=attempted_at,
        last_attempted_at=attempted_at,
        completed_at=attempted_at,
    )
    append_run_log(log_path, f"{status} {source_path.name} -> {final_path}")
    return ImportOutcome(
        final_path=final_path,
        checksum=checksum,
        size_bytes=size_bytes,
        status=status,
        recording_id=recording_id,
        file_id=file_id,
    )
