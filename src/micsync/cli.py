from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import uuid

from micsync.catalog import Catalog
from micsync.config import apply_runtime_overrides, build_config, load_env_file
from micsync.eject import eject_volume
from micsync.importer import derive_mirrored_recording, mirror_recording_to_raw
from micsync.lock import LockManager
from micsync.notify import (
    build_completion_message,
    build_incomplete_message,
    build_start_message,
    send_notification,
)
from micsync.scanner import scan_candidates


@dataclass
class RunSummary:
    imported_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    warning_count: int = 0
    total_bytes: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="micsync",
        description="Import DJI Mic recordings into the Nexus shared recordings root.",
    )
    parser.add_argument("--max-file-size-mb", type=int, default=None)
    parser.add_argument("--notify", default=None)
    parser.add_argument("--eject", default=None)
    return parser


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_config(args: argparse.Namespace):
    nexus_data_root = Path(
        os.environ.get("NEXUS_DATA_ROOT", str(Path.home() / "nexus-data"))
    )
    env = dict(os.environ)
    env.update(load_env_file(nexus_data_root / "micSync" / "config" / "micsync.env"))
    config = build_config(nexus_data_root=nexus_data_root, env=env)
    return apply_runtime_overrides(
        config,
        max_file_size_mb=args.max_file_size_mb,
        notify=_parse_optional_bool(args.notify),
        eject=_parse_optional_bool(args.eject),
    )


def run_import(args: argparse.Namespace) -> int:
    config = _load_config(args)
    run_root = config.runtime_root / "run"
    log_path = config.runtime_root / "logs" / "runs.log"
    lock = LockManager(run_root, stale_timeout_seconds=config.stale_lock_timeout_seconds)
    acquired = lock.acquire_or_request_rescan()
    if not acquired.acquired:
        return 0

    run_started = datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex
    catalog = Catalog(config.recordings_db_path)
    seen_volumes: dict[str, Path] = {}
    summary = RunSummary()
    ejected_volumes: list[str] = []
    ejected_labels: set[str] = set()
    try:
        while True:
            lock.refresh("scanning")
            candidates = scan_candidates(
                allow_extensions=set(config.extension_allowlist),
                max_file_size_mb=config.max_file_size_mb,
                exclude_volume_labels={"Macintosh HD"},
            )
            pending_candidates = [c for c in candidates if c.volume_root.name not in {"Macintosh HD"}]
            pending_candidates.sort(
                key=lambda candidate: (
                    candidate.volume_label,
                    candidate.source_parent_folder,
                    candidate.source_path.name,
                )
            )
            pending_bytes = sum(candidate.file_size_bytes for candidate in pending_candidates)
            if config.notify and pending_candidates:
                send_notification(
                    title="micSync mirror starting",
                    message=build_start_message(
                        candidate_count=len(pending_candidates),
                        total_bytes=pending_bytes,
                    ),
                )

            any_processed = False
            mirrored_outcomes = []
            for candidate in pending_candidates:
                any_processed = True
                seen_volumes[candidate.volume_label] = candidate.volume_root
                lock.refresh(f"mirroring {candidate.source_path.name}")
                try:
                    outcome = mirror_recording_to_raw(
                        source_path=candidate.source_path,
                        source_mount_path=candidate.volume_root,
                        source_parent_folder=candidate.source_parent_folder,
                        volume_label=candidate.volume_label,
                        recordings_root=config.recordings_root,
                        tmp_root=config.recordings_tmp_root,
                        catalog=catalog,
                        log_path=log_path,
                        run_id=run_id,
                    )
                    mirrored_outcomes.append(outcome)
                    summary.total_bytes += outcome.size_bytes
                    summary.warning_count += outcome.warning_count
                    if outcome.status == "duplicate":
                        summary.duplicate_count += 1
                    else:
                        summary.imported_count += 1
                except Exception as exc:  # broad on purpose for run-level robustness
                    summary.failed_count += 1
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(f"{datetime.now(timezone.utc).isoformat()} failed {candidate.source_path} {exc}\n")

            if summary.failed_count == 0 and config.eject:
                for label, volume_root in seen_volumes.items():
                    if label in ejected_labels:
                        continue
                    if eject_volume(volume_root):
                        ejected_volumes.append(label)
                        ejected_labels.add(label)

            for mirrored in mirrored_outcomes:
                lock.refresh(f"deriving {mirrored.raw_path.name}")
                try:
                    derived = derive_mirrored_recording(
                        raw_path=mirrored.raw_path,
                        source_file_id=mirrored.source_file_id,
                        catalog=catalog,
                        log_path=log_path,
                        enable_derived_outputs=config.enable_derived_outputs,
                        derived_root=config.recordings_derived_root,
                        derived_outputs_strategy=config.derived_outputs_strategy,
                        segment_cadence_seconds=config.segment_cadence_seconds,
                        segment_group_tolerance_ms=config.segment_group_tolerance_ms,
                    )
                    summary.warning_count += max(0, derived.warning_count - mirrored.warning_count)
                except Exception as exc:  # broad on purpose for run-level robustness
                    summary.failed_count += 1
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(f"{datetime.now(timezone.utc).isoformat()} failed {mirrored.raw_path} {exc}\n")

            if not any_processed and not lock.consume_rescan_request():
                break
            if not lock.consume_rescan_request():
                break

        elapsed_seconds = int((datetime.now(timezone.utc) - run_started).total_seconds())
        if config.notify:
            if summary.failed_count == 0:
                send_notification(
                    title=(
                        "micSync import complete with warnings"
                        if summary.warning_count > 0
                        else "micSync import complete"
                    ),
                    message=build_completion_message(
                        imported_count=summary.imported_count,
                        duplicate_count=summary.duplicate_count,
                        failed_count=summary.failed_count,
                        warning_count=summary.warning_count,
                        total_bytes=summary.total_bytes,
                        elapsed_seconds=elapsed_seconds,
                        ejected_volumes=ejected_volumes,
                    ),
                )
            else:
                send_notification(
                    title="micSync import incomplete",
                    message=build_incomplete_message(
                        imported_count=summary.imported_count,
                        duplicate_count=summary.duplicate_count,
                        failed_count=summary.failed_count,
                        warning_count=summary.warning_count,
                        total_bytes=summary.total_bytes,
                        elapsed_seconds=elapsed_seconds,
                    ),
                )
        return 0 if summary.failed_count == 0 else 1
    finally:
        lock.release()


def main() -> int:
    args = build_parser().parse_args()
    return run_import(args)


if __name__ == "__main__":
    raise SystemExit(main())
