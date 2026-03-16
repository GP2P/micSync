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
    stop_hint: str | None = None,
) -> str:
    message = f"{candidate_count} candidate files, {_format_bytes(total_bytes)} queued"
    if stop_hint:
        return f"{message} | stop: {stop_hint}"
    return message


def build_stop_command(*, deploy_root: Path, data_root: Path) -> str:
    script_path = deploy_root / "scripts" / "micsync.sh"
    return (
        f'NEXUS_DEPLOY_ROOT={shlex.quote(str(deploy_root))} '
        f'NEXUS_DATA_ROOT={shlex.quote(str(data_root))} '
        f'{shlex.quote(str(script_path))} --stop'
    )


def build_completion_message(
    *,
    imported_count: int,
    duplicate_count: int,
    failed_count: int,
    warning_count: int,
    total_bytes: int,
    elapsed_seconds: int,
    ejected_volumes: list[str],
) -> str:
    parts = [
        f"{imported_count} imported",
        f"{duplicate_count} duplicate",
        f"{failed_count} failed",
        f"{warning_count} warning",
        _format_bytes(total_bytes),
        f"{elapsed_seconds}s",
    ]
    if ejected_volumes:
        parts.append(f"ejected: {', '.join(ejected_volumes)}")
    return " | ".join(parts)


def build_incomplete_message(
    *,
    imported_count: int,
    duplicate_count: int,
    failed_count: int,
    warning_count: int,
    total_bytes: int,
    elapsed_seconds: int,
) -> str:
    return (
        f"incomplete | {imported_count} imported | {duplicate_count} duplicate | "
        f"{failed_count} failed | {warning_count} warning | "
        f"{_format_bytes(total_bytes)} | {elapsed_seconds}s"
    )


def build_stopped_message(
    *,
    imported_count: int,
    duplicate_count: int,
    warning_count: int,
    total_bytes: int,
    elapsed_seconds: int,
) -> str:
    return (
        f"stopped | {imported_count} imported | {duplicate_count} duplicate | "
        f"{warning_count} warning | {_format_bytes(total_bytes)} | {elapsed_seconds}s"
    )


def copy_to_clipboard(text: str) -> bool:
    result = subprocess.run(
        ["pbcopy"],
        input=text,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def send_notification(*, title: str, message: str) -> None:
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
