from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import uuid

from micsync.catalog import Catalog
from micsync.config import apply_runtime_overrides, build_config, load_env_file
from micsync.eject import eject_volume
from micsync.importer import (
    derive_mirrored_recording,
    find_preexisting_raw_duplicate,
    mirror_recording_to_raw,
)
from micsync.lock import LockManager
from micsync.logging_utils import (
    build_event_line,
    build_progress_line,
    build_run_logger,
)
from micsync.notify import (
    build_completion_message,
    build_incomplete_message,
    build_stop_command,
    build_start_message,
    build_stopped_message,
    copy_to_clipboard,
    send_notification,
)
from micsync.scanner import scan_candidates


@dataclass
class RunSummary:
    mirrored_count: int = 0
    derived_count: int = 0
    duplicate_count: int = 0
    rescan_existing_count: int = 0
    failed_count: int = 0
    warning_count: int = 0
    total_bytes: int = 0
    stopped: bool = False


MAX_CONFIRMATION_RESCANS = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="micSync",
        description="Import DJI Mic recordings into the shared recordings root.",
    )
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--max-file-size-mb", type=int, default=None)
    parser.add_argument("--derived", default=None)
    parser.add_argument("--notify", default=None)
    parser.add_argument("--eject", default=None)
    parser.add_argument("--source-volume", action="append", default=None)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--run-detached-child", action="store_true", help=argparse.SUPPRESS)
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
        derived=_parse_optional_bool(getattr(args, "derived", None)),
        notify=_parse_optional_bool(args.notify),
        eject=_parse_optional_bool(args.eject),
    )


def _service_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _data_root(config) -> Path:
    return Path(os.environ.get("NEXUS_DATA_ROOT", str(config.runtime_root.parent)))


def _detached_child_argv(argv: list[str]) -> list[str]:
    child_args = [arg for arg in argv if arg != "--detach"]
    child_args.append("--run-detached-child")
    return child_args


def _launch_detached(argv: list[str]) -> int:
    child_env = dict(os.environ)
    subprocess.Popen(
        [sys.executable, "-m", "micsync.cli", *_detached_child_argv(argv)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=child_env,
        cwd=os.getcwd(),
    )
    return 0


def _resolve_source_volumes(raw_paths: list[str] | None) -> list[Path]:
    if not raw_paths:
        return []

    seen: set[Path] = set()
    resolved: list[Path] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path in seen:
            continue
        seen.add(path)
        resolved.append(path)
    return resolved


def _nearest_existing_path(path: Path) -> Path:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def _mount_point_for_path(path: Path) -> tuple[Path | None, str | None]:
    probe = _nearest_existing_path(path)
    result = subprocess.run(
        ["df", "-P", str(probe)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None, f"df failed for {probe}"

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, f"df returned no mount point for {probe}"

    fields = lines[-1].split()
    if len(fields) < 6:
        return None, f"df returned malformed output for {probe}"
    return Path(fields[-1]), None


def _recordings_root_supports_clone(path: Path) -> tuple[bool, str | None]:
    mount_point, mount_error = _mount_point_for_path(path)
    if mount_point is None:
        return False, mount_error
    try:
        result = subprocess.run(
            ["diskutil", "info", "-plist", str(mount_point)],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return False, "diskutil is unavailable"

    if result.returncode != 0:
        return False, f"diskutil info failed for {mount_point}"

    try:
        info = plistlib.loads(result.stdout)
    except Exception:
        return False, f"diskutil info returned unreadable data for {mount_point}"

    filesystem_type = str(info.get("FilesystemType") or "").strip()
    if filesystem_type.lower() == "apfs":
        return True, None
    if filesystem_type:
        return False, f"{mount_point} uses {filesystem_type}, not APFS"
    return False, f"filesystem type is unknown for {mount_point}"


def _preflight_derived_outputs(config) -> tuple[bool, str | None]:
    if not config.enable_derived_outputs:
        return False, None
    return _recordings_root_supports_clone(config.recordings_root)


def _pending_derivation_queue(config, catalog: Catalog) -> list[tuple[int, Path, str, int, int]]:
    pending_rows = catalog.fetch_pending_source_files_for_derivation()
    queue: list[tuple[int, Path, str, int, int]] = []
    for row in pending_rows:
        raw_relative_path = row["raw_relative_path"]
        if not raw_relative_path:
            continue
        row_keys = row.keys() if hasattr(row, "keys") else ()
        existing_warning_count = (
            1 if "error_detail" in row_keys and row["error_detail"] else 0
        )
        source_size_bytes = int(row["source_size_bytes"] or 0) if "source_size_bytes" in row_keys else 0
        queue.append(
            (
                int(row["id"]),
                config.recordings_root / str(raw_relative_path),
                str(row["source_filename"]),
                existing_warning_count,
                source_size_bytes,
            )
        )
    return queue


def _unpack_derivation_queue_item(
    item: tuple[int, Path, str, int] | tuple[int, Path, str, int, int],
) -> tuple[int, Path, str, int, int]:
    if len(item) == 5:
        return item
    source_file_id, raw_path, source_filename, existing_warning_count = item
    fallback_size_bytes = raw_path.stat().st_size if raw_path.exists() else 0
    return (
        source_file_id,
        raw_path,
        source_filename,
        existing_warning_count,
        fallback_size_bytes,
    )


def _planned_scan_volume_roots(source_volumes: list[Path]) -> list[Path]:
    if source_volumes:
        return source_volumes
    volumes_root = Path("/Volumes")
    if not volumes_root.exists():
        return []
    return sorted(
        path
        for path in volumes_root.iterdir()
        if path.is_dir() and path.name not in {"Macintosh HD"}
    )


def _currently_attached_volume_roots(source_volumes: list[Path]) -> list[Path]:
    if source_volumes:
        return [path for path in source_volumes if path.is_dir()]
    return _planned_scan_volume_roots(source_volumes)


def run_import(args: argparse.Namespace) -> int:
    config = _load_config(args)
    run_root = config.runtime_root / "run"
    log_path = config.runtime_root / "logs" / "runs.log"
    log_event = build_run_logger(
        log_path=log_path,
        echo_to_stdout=not getattr(args, "run_detached_child", False),
    )
    lock = LockManager(run_root, stale_timeout_seconds=config.stale_lock_timeout_seconds)
    stop_command = build_stop_command(
        service_root=_service_root(),
        data_root=_data_root(config),
    )
    if args.stop:
        stop_requested = lock.request_stop()
        log_event(
            build_event_line(
                "micSync stop requested"
                if stop_requested
                else "micSync stop ignored; no active import is running",
                kind="stop",
            )
        )
        if config.notify:
            if stop_requested:
                send_notification(
                    title="micSync stop requested",
                    message="graceful stop requested; current file will finish first",
                )
            else:
                send_notification(
                    title="micSync stop ignored",
                    message="no active import is running",
                )
        return 0
    acquired = lock.acquire_or_request_rescan()
    if not acquired.acquired:
        log_event(
            build_event_line(
                "micSync lock busy requested_rescan="
                f"{str(acquired.requested_rescan).lower()}",
                kind="lock",
            )
        )
        return 0

    run_started = datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex
    catalog = Catalog(config.recordings_db_path)
    seen_volumes: dict[str, Path] = {}
    summary = RunSummary()
    ejected_volumes: list[str] = []
    signal_stop_requested = {"value": False}
    source_volumes = _resolve_source_volumes(getattr(args, "source_volume", None))

    def _request_graceful_stop(signum: int, _frame) -> None:
        signal_stop_requested["value"] = True
        lock.request_stop()

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _request_graceful_stop)
    signal.signal(signal.SIGTERM, _request_graceful_stop)
    try:
        def emit_notification(*, title: str, message: str) -> None:
            send_notification(title=title, message=message)
            log_event(build_event_line(f"micSync sent notification title={title}", kind="event"))

        log_event(
            build_event_line(
                "micSync run started "
                f"run_id={run_id} "
                f"detached={str(getattr(args, 'run_detached_child', False)).lower()}",
                kind="run",
            )
        )
        if source_volumes:
            log_event(
                build_event_line(
                    "micSync source volumes "
                    + ", ".join(str(path) for path in source_volumes),
                    kind="event",
                )
            )
        derived_enabled, derived_reason = _preflight_derived_outputs(config)
        if config.enable_derived_outputs and not derived_enabled:
            log_event(
                build_event_line(
                    f"micSync derived outputs disabled: {derived_reason}",
                    kind="event",
                )
            )
        config = apply_runtime_overrides(
            config,
            max_file_size_mb=None,
            derived=derived_enabled,
            notify=None,
            eject=None,
        )
        if acquired.recovered_stale_lock:
            log_event(build_event_line("micSync recovered stale lock", kind="lock"))
        pass_index = 0
        completed_rescans = 0
        while True:
            is_rescan_pass = pass_index > 0
            if signal_stop_requested["value"] or lock.consume_stop_request():
                summary.stopped = True
                log_event(build_event_line("micSync stop requested before scan", kind="stop"))
                break
            lock.refresh("scanning")
            scan_volume_roots = _planned_scan_volume_roots(source_volumes)
            log_event(
                build_event_line(
                    f"micSync scan started volumes={len(scan_volume_roots)}",
                    kind="scan",
                )
            )

            def on_volume_start(volume_root: Path) -> None:
                log_event(
                    build_event_line(
                        f"micSync scan volume started label={volume_root.name} path={volume_root}",
                        kind="scan",
                    )
                )

            def on_volume_complete(volume_root: Path, candidate_count: int) -> None:
                log_event(
                    build_event_line(
                        f"micSync scan volume complete label={volume_root.name} candidates={candidate_count}",
                        kind="scan",
                    )
                )

            candidates = scan_candidates(
                allow_extensions=set(config.extension_allowlist),
                max_file_size_mb=config.max_file_size_mb,
                exclude_volume_labels={"Macintosh HD"},
                include_volume_roots=source_volumes or None,
                on_volume_start=on_volume_start,
                on_volume_complete=on_volume_complete,
            )
            pending_candidates = [c for c in candidates if c.volume_root.name not in {"Macintosh HD"}]
            for candidate in pending_candidates:
                seen_volumes[candidate.volume_label] = candidate.volume_root
            log_event(
                build_event_line(
                    f"micSync scan complete candidates={len(pending_candidates)} volumes={len(scan_volume_roots)}",
                    kind="scan",
                )
            )
            pending_candidates.sort(
                key=lambda candidate: (
                    candidate.volume_label,
                    candidate.source_parent_folder,
                    candidate.source_path.name,
                )
            )
            log_event(
                build_event_line(
                    f"micSync duplicate preflight started candidates={len(pending_candidates)}",
                    kind="event",
                )
            )
            preexisting_duplicates = []
            mirror_candidates = []
            for candidate in pending_candidates:
                duplicate_raw_path = find_preexisting_raw_duplicate(
                    source_path=candidate.source_path,
                    source_parent_folder=candidate.source_parent_folder,
                    volume_label=candidate.volume_label,
                    recordings_root=config.recordings_root,
                )
                if duplicate_raw_path is not None:
                    preexisting_duplicates.append((candidate, duplicate_raw_path))
                else:
                    mirror_candidates.append(candidate)
            duplicate_only_volumes: dict[str, Path] = {}
            mirror_volume_labels = {candidate.volume_label for candidate in mirror_candidates}
            for candidate, _ in preexisting_duplicates:
                if candidate.volume_label in mirror_volume_labels:
                    continue
                duplicate_only_volumes[candidate.volume_label] = candidate.volume_root
            pending_candidates = mirror_candidates
            if is_rescan_pass:
                summary.rescan_existing_count = len(preexisting_duplicates)
            else:
                summary.duplicate_count += len(preexisting_duplicates)
            log_event(
                build_event_line(
                    "micSync duplicate preflight complete "
                    f"new={len(pending_candidates)} "
                    f"existing={len(preexisting_duplicates)} "
                    f"duplicate_only_volumes={len(duplicate_only_volumes)}",
                    kind="event",
                )
            )
            pending_bytes = sum(candidate.file_size_bytes for candidate in pending_candidates)
            if pending_candidates or preexisting_duplicates:
                log_event(
                    build_event_line(
                        "micSync mirror starting "
                        f"candidates={len(pending_candidates)} "
                        f"existing={len(preexisting_duplicates)} "
                        f"total={pending_bytes / 1_000_000:.0f}MB",
                        kind="event",
                    )
                )
            else:
                log_event(build_event_line("micSync no candidates detected", kind="event"))
            if config.notify and pending_candidates:
                stop_hint = None
                if copy_to_clipboard(stop_command):
                    stop_hint = "copied exact stop command to clipboard"
                    log_event(build_event_line("micSync copied stop command to clipboard", kind="event"))
                else:
                    stop_hint = stop_command
                    log_event(
                        build_event_line(
                            "micSync failed to copy stop command to clipboard; using literal command",
                            kind="warn",
                        )
                    )
                emit_notification(
                    title="micSync mirror starting",
                    message=build_start_message(
                        candidate_count=len(pending_candidates),
                        total_bytes=pending_bytes,
                        existing_count=len(preexisting_duplicates),
                        stop_hint=stop_hint,
                    ),
                )
            processed_mirror_bytes = 0
            for mirror_index, candidate in enumerate(pending_candidates, start=1):
                if signal_stop_requested["value"] or lock.consume_stop_request():
                    summary.stopped = True
                    log_event(build_event_line("micSync stop requested during mirror stage", kind="stop"))
                    break
                seen_volumes[candidate.volume_label] = candidate.volume_root
                lock.refresh(f"mirroring {candidate.source_path.name}")
                try:
                    outcome = mirror_recording_to_raw(
                        source_path=candidate.source_path,
                        source_mount_path=candidate.volume_root,
                        source_parent_folder=candidate.source_parent_folder,
                        volume_label=candidate.volume_label,
                        hidden=candidate.hidden,
                        recordings_root=config.recordings_root,
                        tmp_root=config.recordings_tmp_root,
                        catalog=catalog,
                        log_path=log_path,
                        log_event=log_event,
                        run_id=run_id,
                    )
                    summary.total_bytes += outcome.size_bytes
                    processed_mirror_bytes += outcome.size_bytes
                    summary.warning_count += outcome.warning_count
                    if outcome.status == "duplicate":
                        summary.duplicate_count += 1
                    else:
                        summary.mirrored_count += 1
                    log_event(
                        build_progress_line(
                            action="mirror",
                            current_index=mirror_index,
                            total_count=len(pending_candidates),
                            processed_bytes=processed_mirror_bytes,
                            total_bytes=pending_bytes,
                            file_size_bytes=outcome.size_bytes,
                            path=str(outcome.raw_path.relative_to(config.recordings_root)),
                        )
                    )
                    if outcome.status == "duplicate":
                        log_event(
                            build_event_line(
                                f"micSync duplicate already mirrored {outcome.raw_path.relative_to(config.recordings_root)}",
                                kind="event",
                            )
                        )
                except Exception as exc:  # broad on purpose for run-level robustness
                    summary.failed_count += 1
                    log_event(
                        build_event_line(
                            "micSync failed "
                            f"phase=mirror path={candidate.source_path} error={exc}",
                            kind="fail",
                        )
                    )

            if summary.stopped:
                log_event(build_event_line("micSync stopped after mirror phase request", kind="stop"))

            derivation_queue = [
                _unpack_derivation_queue_item(item)
                for item in _pending_derivation_queue(config, catalog)
            ]
            derivation_total_bytes = sum(
                size_bytes
                for _, _, _, _, size_bytes in derivation_queue
            )
            if derivation_queue:
                log_event(
                    build_event_line(
                        "micSync derive starting "
                        f"candidates={len(derivation_queue)} "
                        f"total={derivation_total_bytes / 1_000_000:.0f}MB",
                        kind="event",
                    )
                )
            else:
                log_event(build_event_line("micSync derive no pending candidates", kind="event"))
            processed_normalize_bytes = 0
            processed_derivations = 0
            for normalize_index, (
                source_file_id,
                raw_path,
                source_filename,
                existing_warning_count,
                source_size_bytes,
            ) in enumerate(derivation_queue, start=1):
                if signal_stop_requested["value"] or lock.consume_stop_request():
                    summary.stopped = True
                    log_event(build_event_line("micSync stop requested during derive stage", kind="stop"))
                    break
                lock.refresh(f"deriving {source_filename}")
                try:
                    derived = derive_mirrored_recording(
                        raw_path=raw_path,
                        source_file_id=source_file_id,
                        catalog=catalog,
                        log_path=log_path,
                        log_event=log_event,
                        enable_derived_outputs=config.enable_derived_outputs,
                        derived_root=config.recordings_derived_root,
                        derived_outputs_strategy=config.derived_outputs_strategy,
                        segment_cadence_seconds=config.segment_cadence_seconds,
                        segment_group_tolerance_ms=config.segment_group_tolerance_ms,
                    )
                    summary.derived_count += 1
                    summary.warning_count += max(
                        0, derived.warning_count - existing_warning_count
                    )
                    derived_path = getattr(derived, "derived_path", None)
                    if isinstance(derived_path, Path):
                        processed_normalize_bytes += source_size_bytes
                        log_event(
                            build_progress_line(
                                action="normalize",
                                current_index=normalize_index,
                                total_count=len(derivation_queue),
                                processed_bytes=processed_normalize_bytes,
                                total_bytes=derivation_total_bytes,
                                file_size_bytes=int(getattr(derived, "size_bytes", source_size_bytes)),
                                path=str(derived_path.relative_to(config.recordings_root)),
                            )
                        )
                    processed_derivations += 1
                except Exception as exc:  # broad on purpose for run-level robustness
                    summary.failed_count += 1
                    log_event(
                        build_event_line(
                            "micSync failed "
                            f"phase=derive path={raw_path} error={exc}",
                            kind="fail",
                        )
                    )
            if not summary.stopped:
                log_event(
                    build_event_line(
                        f"micSync derive complete processed={processed_derivations}",
                        kind="event",
                    )
                )

            if summary.stopped:
                break
            if summary.failed_count > 0:
                log_event(
                    build_event_line(
                        "micSync failures detected; skipping confirmatory rescans",
                        kind="event",
                    )
                )
                break
            rescan_requested = lock.consume_rescan_request()
            if pass_index == 0:
                completed_rescans = 1
                pass_index += 1
                log_event(build_event_line("micSync confirmatory rescan starting", kind="event"))
                continue

            if len(pending_candidates) == 0 and not rescan_requested:
                if summary.failed_count == 0 and config.eject and not summary.stopped:
                    attached_volumes = {
                        label: volume_root
                        for label, volume_root in seen_volumes.items()
                        if volume_root.is_dir()
                    }
                    if attached_volumes:
                        log_event(build_event_line("micSync rescan stable; attempting eject", kind="event"))
                    for label, volume_root in attached_volumes.items():
                        if eject_volume(volume_root):
                            ejected_volumes.append(label)
                            log_event(build_event_line(f"micSync ejected volume {label}", kind="eject"))
                        else:
                            log_event(build_event_line(f"micSync failed to eject volume {label}", kind="fail"))
                log_event(build_event_line("micSync run cycle complete after stable rescan", kind="event"))
                break

            if completed_rescans >= MAX_CONFIRMATION_RESCANS:
                summary.warning_count += 1
                log_event(
                    build_event_line(
                        f"micSync rescan cap reached count={MAX_CONFIRMATION_RESCANS}",
                        kind="warn",
                    )
                )
                break

            completed_rescans += 1
            pass_index += 1
            if rescan_requested:
                log_event(build_event_line("micSync rescan requested; continuing", kind="event"))
            else:
                log_event(build_event_line("micSync rescan not yet stable; continuing", kind="event"))
            continue

        elapsed_seconds = int((datetime.now(timezone.utc) - run_started).total_seconds())
        log_event(
            build_event_line(
                "summary "
                f"mirrored={summary.mirrored_count} "
                f"derived={summary.derived_count} "
                f"duplicate={summary.duplicate_count} "
                f"rescan_existing={summary.rescan_existing_count} "
                f"failed={summary.failed_count} "
                f"warning={summary.warning_count} "
                f"total={summary.total_bytes / 1_000_000:.0f}MB "
                f"elapsed_seconds={elapsed_seconds}",
                kind="summary",
            )
        )
        if config.notify:
            if summary.stopped:
                emit_notification(
                    title="micSync import stopped",
                    message=build_stopped_message(
                        mirrored_count=summary.mirrored_count,
                        derived_count=summary.derived_count,
                        duplicate_count=summary.duplicate_count,
                        warning_count=summary.warning_count,
                        total_bytes=summary.total_bytes,
                        elapsed_seconds=elapsed_seconds,
                    ),
                )
            elif summary.failed_count == 0:
                emit_notification(
                    title=(
                        "micSync import complete with warnings"
                        if summary.warning_count > 0
                        else "micSync import complete"
                    ),
                    message=build_completion_message(
                        mirrored_count=summary.mirrored_count,
                        derived_count=summary.derived_count,
                        duplicate_count=summary.duplicate_count,
                        rescan_existing=summary.rescan_existing_count,
                        failed_count=summary.failed_count,
                        warning_count=summary.warning_count,
                        total_bytes=summary.total_bytes,
                        elapsed_seconds=elapsed_seconds,
                        ejected_volumes=ejected_volumes,
                    ),
                )
            else:
                emit_notification(
                    title="micSync import incomplete",
                    message=build_incomplete_message(
                        mirrored_count=summary.mirrored_count,
                        derived_count=summary.derived_count,
                        duplicate_count=summary.duplicate_count,
                        failed_count=summary.failed_count,
                        warning_count=summary.warning_count,
                        total_bytes=summary.total_bytes,
                        elapsed_seconds=elapsed_seconds,
                    ),
                )
        return 0 if summary.failed_count == 0 else 1
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        lock.release()


def main() -> int:
    argv = sys.argv[1:]
    args = build_parser().parse_args(argv)
    if args.detach and not args.stop and not args.run_detached_child:
        return _launch_detached(argv)
    return run_import(args)


if __name__ == "__main__":
    raise SystemExit(main())
