from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket


@dataclass(frozen=True)
class LockAcquireResult:
    acquired: bool
    recovered_stale_lock: bool
    requested_rescan: bool


class LockManager:
    def __init__(self, run_root: Path, stale_timeout_seconds: int = 300) -> None:
        self.run_root = run_root
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.run_root / "active.lock"
        self.rescan_path = self.run_root / "rescan.request"
        self.stop_path = self.run_root / "stop.request"
        self.stale_timeout = timedelta(seconds=stale_timeout_seconds)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _read_lock(self) -> dict[str, str] | None:
        if not self.lock_path.exists():
            return None
        return json.loads(self.lock_path.read_text())

    def _write_lock(self, payload: dict[str, str]) -> None:
        self.lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def _pid_is_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def write_stale_lock_for_test(self, *, pid: int, heartbeat_age_seconds: int) -> None:
        heartbeat_at = self._now() - timedelta(seconds=heartbeat_age_seconds)
        self._write_lock(
            {
                "pid": pid,
                "hostname": socket.gethostname(),
                "started_at": heartbeat_at.isoformat(),
                "last_heartbeat_at": heartbeat_at.isoformat(),
                "phase": "test",
            }
        )

    def acquire_or_request_rescan(self) -> LockAcquireResult:
        payload = self._read_lock()
        if payload is not None:
            pid = int(payload.get("pid", 0))
            heartbeat_at = datetime.fromisoformat(payload["last_heartbeat_at"])
            is_stale = (self._now() - heartbeat_at) > self.stale_timeout
            if self._pid_is_alive(pid) and not is_stale:
                self.request_rescan()
                return LockAcquireResult(
                    acquired=False,
                    recovered_stale_lock=False,
                    requested_rescan=True,
                )
            self.lock_path.unlink(missing_ok=True)
            self._write_current_lock()
            return LockAcquireResult(
                acquired=True,
                recovered_stale_lock=True,
                requested_rescan=False,
            )

        self._write_current_lock()
        return LockAcquireResult(
            acquired=True,
            recovered_stale_lock=False,
            requested_rescan=False,
        )

    def _write_current_lock(self) -> None:
        now = self._now().isoformat()
        self._write_lock(
            {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "started_at": now,
                "last_heartbeat_at": now,
                "phase": "startup",
            }
        )

    def refresh(self, phase: str) -> None:
        payload = self._read_lock()
        if payload is None:
            return
        payload["phase"] = phase
        payload["last_heartbeat_at"] = self._now().isoformat()
        self._write_lock(payload)

    def request_rescan(self) -> None:
        self.rescan_path.write_text("1\n")

    def consume_rescan_request(self) -> bool:
        if not self.rescan_path.exists():
            return False
        self.rescan_path.unlink()
        return True

    def has_active_owner(self) -> bool:
        payload = self._read_lock()
        if payload is None:
            return False
        pid = int(payload.get("pid", 0))
        heartbeat_at = datetime.fromisoformat(payload["last_heartbeat_at"])
        is_stale = (self._now() - heartbeat_at) > self.stale_timeout
        return self._pid_is_alive(pid) and not is_stale

    def request_stop(self) -> bool:
        if not self.has_active_owner():
            return False
        self.stop_path.write_text("1\n")
        return True

    def consume_stop_request(self) -> bool:
        if not self.stop_path.exists():
            return False
        self.stop_path.unlink()
        return True

    def release(self) -> None:
        self.lock_path.unlink(missing_ok=True)
