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
    take_id: int
    segment_id: int
    artifact_id: int
    warning_count: int


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


def _should_group_with_previous(
    *,
    previous_segment_end_at: str | None,
    previous_duration_ms: int | None,
    current_start_at: datetime,
    segment_cadence_seconds: int,
    segment_group_tolerance_ms: int,
) -> bool:
    if previous_segment_end_at is None or previous_duration_ms is None:
        return False

    cadence_ms = segment_cadence_seconds * 1000
    if abs(previous_duration_ms - cadence_ms) > segment_group_tolerance_ms:
        return False

    previous_end_at = datetime.fromisoformat(previous_segment_end_at)
    delta_ms = abs(int((current_start_at - previous_end_at).total_seconds() * 1000))
    return delta_ms <= segment_group_tolerance_ms


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
    segment_cadence_seconds: int = 1800,
    segment_group_tolerance_ms: int = 1000,
) -> ImportOutcome:
    parsed: ParsedRecordingName = parse_recording_name(source_path.name)
    segment_key = parsed.recording_group_key
    recording_start_at = parsed.start_at.isoformat(timespec="seconds")
    duration_ms = read_duration_ms(source_path)
    recording_end_at = derive_end_time(recording_start_at, duration_ms)
    physical_mic_id = parse_physical_mic_id(volume_label)
    warning_messages: list[str] = []
    if source_path.stat().st_size == 0:
        warning_messages.append(
            "zero-byte source file; recording may be incomplete and end time unavailable"
        )

    previous_segment = catalog.find_latest_segment_for_session(
        tx_slot=parsed.tx_slot,
        physical_mic_id=physical_mic_id,
        source_parent_folder=source_parent_folder,
        before_start_at=recording_start_at,
    )
    if previous_segment and _should_group_with_previous(
        previous_segment_end_at=previous_segment["segment_end_at"],
        previous_duration_ms=previous_segment["duration_ms"],
        current_start_at=parsed.start_at,
        segment_cadence_seconds=segment_cadence_seconds,
        segment_group_tolerance_ms=segment_group_tolerance_ms,
    ):
        take_key = str(previous_segment["take_key"])
        segment_index = int(previous_segment["segment_index"] or 0) + 1
    else:
        take_key = segment_key
        segment_index = 0

    take_id = catalog.upsert_take(
        take_key=take_key,
        take_start_at=recording_start_at,
        take_end_at=recording_end_at,
        tx_slot=parsed.tx_slot,
        physical_mic_id=physical_mic_id,
        source_parent_folder=source_parent_folder,
        health_status="warning" if warning_messages else "ok",
    )
    attempted_at = datetime.now().isoformat(timespec="seconds")
    segment_id = catalog.upsert_segment(
        take_id=take_id,
        segment_key=segment_key,
        segment_index=segment_index,
        segment_start_at=recording_start_at,
        segment_end_at=recording_end_at,
        tx_slot=parsed.tx_slot,
        mic_sequence=parsed.mic_sequence,
        physical_mic_id=physical_mic_id,
        source_parent_folder=source_parent_folder,
        duration_ms=duration_ms,
        first_seen_at=attempted_at,
        last_attempted_at=attempted_at,
        completed_at=attempted_at,
        health_status="warning" if warning_messages else "ok",
        anomaly_code="zero_byte_source" if warning_messages else None,
        anomaly_detail="; ".join(warning_messages) if warning_messages else None,
    )

    tmp_path = tmp_root / f"{segment_key}{source_path.suffix}.tmp"
    checksum, size_bytes = _copy_with_checksum(source_path, tmp_path)
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

    artifact_id = catalog.insert_artifact(
        take_id=take_id,
        segment_id=segment_id,
        segment_key=segment_key,
        run_id=run_id,
        source_volume_label=volume_label,
        source_volume_identifier=volume_label,
        source_mount_path=str(source_mount_path),
        source_parent_folder=source_parent_folder,
        source_filename=source_path.name,
        source_relative_path=str(Path(source_parent_folder) / source_path.name),
        source_size_bytes=size_bytes,
        source_checksum=checksum,
        artifact_start_at=recording_start_at,
        artifact_end_at=recording_end_at,
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
        error_phase="source_validation" if warning_messages else None,
        error_detail="; ".join(warning_messages) if warning_messages else None,
    )
    for warning_message in warning_messages:
        append_run_log(log_path, f"warning {source_path.name}: {warning_message}")
    append_run_log(log_path, f"{status} {source_path.name} -> {final_path}")
    return ImportOutcome(
        final_path=final_path,
        checksum=checksum,
        size_bytes=size_bytes,
        status=status,
        take_id=take_id,
        segment_id=segment_id,
        artifact_id=artifact_id,
        warning_count=len(warning_messages),
    )
