from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
from typing import Callable

from micsync.audio import derive_end_time, read_duration_ms
from micsync.catalog import Catalog
from micsync.logging_utils import append_run_log
from micsync.parser import ParsedRecordingName, parse_physical_mic_id, parse_recording_name


@dataclass(frozen=True)
class MirrorOutcome:
    raw_path: Path
    checksum: str
    size_bytes: int
    status: str
    source_file_id: int
    warning_count: int


@dataclass(frozen=True)
class ImportOutcome:
    raw_path: Path
    checksum: str
    size_bytes: int
    status: str
    take_id: int
    segment_id: int
    source_file_id: int
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


def _raw_source_dir_name(volume_label: str | None, physical_mic_id: int) -> str:
    if physical_mic_id > 0:
        return f"MIC_{physical_mic_id:02d}"
    if volume_label:
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", volume_label.strip()).strip("_")
        if normalized:
            return normalized
    return "UNKNOWN"


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


def mirror_recording_to_raw(
    *,
    source_path: Path,
    source_mount_path: Path,
    source_parent_folder: str,
    volume_label: str | None,
    recordings_root: Path,
    tmp_root: Path,
    catalog: Catalog,
    log_path: Path,
    run_id: str | None = None,
) -> MirrorOutcome:
    parsed: ParsedRecordingName = parse_recording_name(source_path.name)
    recording_start_at = parsed.start_at.isoformat(timespec="seconds")
    duration_ms = read_duration_ms(source_path)
    recording_end_at = derive_end_time(recording_start_at, duration_ms)
    physical_mic_id = parse_physical_mic_id(volume_label)
    source_dir_name = _raw_source_dir_name(volume_label, physical_mic_id)
    warning_messages: list[str] = []
    if source_path.stat().st_size == 0:
        warning_messages.append(
            "zero-byte source file; recording may be incomplete and end time unavailable"
        )

    tmp_path = tmp_root / source_dir_name / source_parent_folder / f"{source_path.name}.tmp"
    checksum, size_bytes = _copy_with_checksum(source_path, tmp_path)
    raw_relative_dir = Path("raw") / source_dir_name / source_parent_folder
    raw_path = plan_destination_path(
        recordings_root=recordings_root,
        relative_dir=raw_relative_dir,
        dest_name=source_path.name,
        incoming_checksum=checksum,
        existing_checksum_lookup=compute_file_checksum,
    )

    status = "mirrored"
    if raw_path.exists():
        status = "duplicate"
        tmp_path.unlink(missing_ok=True)
    else:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.replace(raw_path)

    attempted_at = datetime.now().isoformat(timespec="seconds")
    source_file_id = catalog.upsert_source_file(
        source_key=str(raw_path.relative_to(recordings_root)),
        segment_id=None,
        source_volume_label=volume_label,
        source_volume_identifier=volume_label,
        source_mount_path=str(source_mount_path),
        source_parent_folder=source_parent_folder,
        source_filename=source_path.name,
        source_relative_path=str(Path(source_parent_folder) / source_path.name),
        physical_mic_id=physical_mic_id,
        raw_relative_path=str(raw_path.relative_to(recordings_root)),
        source_size_bytes=size_bytes,
        source_checksum=checksum,
        recording_start_at=recording_start_at,
        recording_end_at=recording_end_at,
        duration_ms=duration_ms,
        variant=parsed.variant,
        mirror_status=status,
        first_seen_at=attempted_at,
        last_attempted_at=attempted_at,
        mirrored_at=attempted_at,
        error_phase="source_validation" if warning_messages else None,
        error_detail="; ".join(warning_messages) if warning_messages else None,
    )
    for warning_message in warning_messages:
        append_run_log(log_path, f"warning {source_path.name}: {warning_message}")
    append_run_log(log_path, f"{status} {source_path.name} -> {raw_path}")
    return MirrorOutcome(
        raw_path=raw_path,
        checksum=checksum,
        size_bytes=size_bytes,
        status=status,
        source_file_id=source_file_id,
        warning_count=len(warning_messages),
    )


def derive_mirrored_recording(
    *,
    raw_path: Path,
    source_file_id: int,
    catalog: Catalog,
    log_path: Path,
    segment_cadence_seconds: int = 1800,
    segment_group_tolerance_ms: int = 1000,
) -> ImportOutcome:
    source_file = catalog.fetch_source_file(source_file_id)
    parsed: ParsedRecordingName = parse_recording_name(str(source_file["source_filename"]))
    segment_key = parsed.recording_group_key
    recording_start_at = str(source_file["recording_start_at"])
    recording_end_at = source_file["recording_end_at"]
    duration_ms = source_file["duration_ms"]
    physical_mic_id = int(source_file["physical_mic_id"])
    warning_messages: list[str] = []
    error_detail = source_file["error_detail"]
    if error_detail:
        warning_messages.append(str(error_detail))

    previous_segment = catalog.find_latest_segment_for_session(
        tx_slot=parsed.tx_slot,
        physical_mic_id=physical_mic_id,
        source_parent_folder=str(source_file["source_parent_folder"]),
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
        take_end_at=str(recording_end_at) if recording_end_at is not None else None,
        tx_slot=parsed.tx_slot,
        physical_mic_id=physical_mic_id,
        source_parent_folder=str(source_file["source_parent_folder"]),
        health_status="warning" if warning_messages else "ok",
    )
    attempted_at = datetime.now().isoformat(timespec="seconds")
    segment_id = catalog.upsert_segment(
        take_id=take_id,
        segment_key=segment_key,
        segment_index=segment_index,
        segment_start_at=recording_start_at,
        segment_end_at=str(recording_end_at) if recording_end_at is not None else None,
        tx_slot=parsed.tx_slot,
        mic_sequence=parsed.mic_sequence,
        physical_mic_id=physical_mic_id,
        source_parent_folder=str(source_file["source_parent_folder"]),
        duration_ms=duration_ms,
        first_seen_at=attempted_at,
        last_attempted_at=attempted_at,
        completed_at=attempted_at,
        health_status="warning" if warning_messages else "ok",
        anomaly_code="zero_byte_source" if warning_messages else None,
        anomaly_detail="; ".join(warning_messages) if warning_messages else None,
    )
    catalog.assign_source_file_to_segment(source_file_id=source_file_id, segment_id=segment_id)
    append_run_log(log_path, f"derived {raw_path.name} -> take {take_id} segment {segment_id}")
    return ImportOutcome(
        raw_path=raw_path,
        checksum=str(source_file["source_checksum"]),
        size_bytes=int(source_file["source_size_bytes"]),
        status=str(source_file["mirror_status"]),
        take_id=take_id,
        segment_id=segment_id,
        source_file_id=source_file_id,
        warning_count=len(warning_messages),
    )


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
    mirrored = mirror_recording_to_raw(
        source_path=source_path,
        source_mount_path=source_mount_path,
        source_parent_folder=source_parent_folder,
        volume_label=volume_label,
        recordings_root=recordings_root,
        tmp_root=tmp_root,
        catalog=catalog,
        log_path=log_path,
        run_id=run_id,
    )
    return derive_mirrored_recording(
        raw_path=mirrored.raw_path,
        source_file_id=mirrored.source_file_id,
        catalog=catalog,
        log_path=log_path,
        segment_cadence_seconds=segment_cadence_seconds,
        segment_group_tolerance_ms=segment_group_tolerance_ms,
    )
