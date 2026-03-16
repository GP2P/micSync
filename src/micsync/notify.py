from __future__ import annotations

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


def build_start_message(*, candidate_count: int, total_bytes: int) -> str:
    return f"{candidate_count} candidate files, {_format_bytes(total_bytes)} queued"


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
