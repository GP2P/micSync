from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

RunLogger = Callable[[str], None]


def append_run_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{message}\n")


def rotate_run_log_if_oversized(
    *,
    log_path: Path,
    max_bytes: int,
    when: datetime | None = None,
) -> Path | None:
    if not log_path.exists():
        return None
    size_bytes = log_path.stat().st_size
    if size_bytes <= max_bytes:
        return None
    append_run_log(
        log_path,
        build_event_line(
            "micSync rotating oversized log "
            f"size_bytes={size_bytes} threshold_bytes={max_bytes}",
            kind="event",
            when=when,
        ),
    )
    rotated_path = log_path.with_name(
        f"{log_path.stem}-{format_log_timestamp(when).replace('.', '').replace(':', '').replace(' ', '-')}{log_path.suffix}"
    )
    log_path.replace(rotated_path)
    return rotated_path


def build_run_logger(*, log_path: Path, echo_to_stdout: bool) -> RunLogger:
    def log_event(message: str) -> None:
        append_run_log(log_path, message)
        if echo_to_stdout:
            print(message, flush=True)

    return log_event


def _resolve_timestamp(when: datetime | None) -> datetime:
    if when is None:
        return datetime.now().astimezone()
    if when.tzinfo is None:
        return when
    return when.astimezone()


def format_log_timestamp(when: datetime | None = None) -> str:
    return _resolve_timestamp(when).strftime("%y.%m.%d %H:%M:%S")


def _format_integer_mb(size_bytes: int, *, width: int) -> str:
    size_token = f"{size_bytes / 1_000_000:.0f}MB"
    return size_token.rjust(width)


def _format_decimal_mb(size_bytes: int) -> str:
    size_mb = size_bytes / 1_000_000
    return f"{size_mb:>6.2f}MB"


def _format_progress_gb_and_percent(processed_bytes: int, total_bytes: int) -> str:
    processed_gb = processed_bytes / 1_000_000_000
    total_gb = total_bytes / 1_000_000_000 if total_bytes else 0
    percent = round((processed_bytes / total_bytes) * 100) if total_bytes else 0
    return f"{processed_gb:>4.1f}/{total_gb:>4.1f} GB, {percent:>3.0f}%"


def build_event_line(message: str, *, kind: str = "event", when: datetime | None = None) -> str:
    return f"{format_log_timestamp(when)} | {kind:<9} | {message}"


def build_progress_line(
    *,
    action: str,
    current_index: int,
    total_count: int,
    processed_bytes: int,
    total_bytes: int,
    file_size_bytes: int,
    path: str,
    when: datetime | None = None,
) -> str:
    count_width = max(2, len(str(total_count)))
    return (
        f"{format_log_timestamp(when)} | {action:<9} | "
        f"{current_index:>{count_width}}/{total_count} | "
        f"{_format_progress_gb_and_percent(processed_bytes, total_bytes)} | "
        f"{_format_decimal_mb(file_size_bytes)} | "
        f"{path}"
    )
