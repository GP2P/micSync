from __future__ import annotations

from pathlib import Path
import shlex
import subprocess


def _format_bytes(total_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(total_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{int(total_bytes)}B"


def build_start_message(
    *,
    candidate_count: int,
    total_bytes: int,
    existing_count: int = 0,
    stop_hint: str | None = None,
) -> str:
    message = f"{candidate_count} candidate files, {_format_bytes(total_bytes)} queued"
    if existing_count:
        message = f"{message} | {existing_count} already exist"
    if stop_hint:
        return f"{message} | stop: {stop_hint}"
    return message
def build_stop_command(*, service_root: Path, data_root: Path) -> str:
    script_path = service_root / "scripts" / "micSync.sh"
    return (
        f'MICSYNC_HOME={shlex.quote(str(data_root))} '
        f'{shlex.quote(str(script_path))} --stop'
    )


def build_completion_message(
    *,
    mirrored_count: int,
    derived_count: int,
    duplicate_count: int,
    rescan_existing: int,
    failed_count: int,
    warning_count: int,
    total_bytes: int,
    elapsed_seconds: int,
    ejected_volumes: list[str],
    attached_volumes: list[str],
) -> str:
    parts = [
        f"{mirrored_count} imported",
        f"{derived_count} organized",
        f"{duplicate_count} duplicate",
        f"{rescan_existing} rescan existing",
        f"{failed_count} failed",
        f"{warning_count} warning",
        _format_bytes(total_bytes),
        f"{elapsed_seconds}s",
    ]
    if ejected_volumes:
        parts.append(f"ejected: {', '.join(ejected_volumes)}")
    if attached_volumes:
        parts.append(f"attached: {', '.join(attached_volumes)}")
    return " | ".join(parts)


def build_incomplete_message(
    *,
    mirrored_count: int,
    derived_count: int,
    duplicate_count: int,
    failed_count: int,
    warning_count: int,
    total_bytes: int,
    elapsed_seconds: int,
) -> str:
    return (
        f"incomplete | {mirrored_count} imported | {derived_count} organized | "
        f"{duplicate_count} duplicate | {failed_count} failed | {warning_count} warning | "
        f"{_format_bytes(total_bytes)} | {elapsed_seconds}s"
    )


def build_stopped_message(
    *,
    mirrored_count: int,
    derived_count: int,
    duplicate_count: int,
    warning_count: int,
    total_bytes: int,
    elapsed_seconds: int,
) -> str:
    return (
        f"stopped | {mirrored_count} imported | {derived_count} organized | "
        f"{duplicate_count} duplicate | {warning_count} warning | "
        f"{_format_bytes(total_bytes)} | {elapsed_seconds}s"
    )


def copy_to_clipboard(text: str) -> bool:
    try:
        result = subprocess.run(
            ["pbcopy"],
            input=text,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def send_notification(*, title: str, message: str) -> None:
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{message}" with title "{title}"',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return
