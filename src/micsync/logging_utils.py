from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

RunLogger = Callable[[str], None]


def append_run_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def build_run_logger(*, log_path: Path, echo_to_stdout: bool) -> RunLogger:
    def log_event(message: str) -> None:
        append_run_log(log_path, message)
        if echo_to_stdout:
            print(message, flush=True)

    return log_event
