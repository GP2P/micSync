from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import os
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    runtime_root: Path
    recordings_root: Path
    recordings_raw_root: Path
    recordings_derived_root: Path
    recordings_db_path: Path
    recordings_tmp_root: Path
    max_file_size_mb: int | None
    extension_allowlist: tuple[str, ...]
    variant_policy: str
    enable_derived_outputs: bool
    derived_outputs_strategy: str
    segment_cadence_seconds: int
    segment_group_tolerance_ms: int
    stale_lock_timeout_seconds: int
    notify: bool
    eject: bool
    organized_layout: str = "timeline"


def _coerce_optional_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value)


def _coerce_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _expand_path(value: str, env: Mapping[str, str]) -> Path:
    expanded = value
    for _ in range(2):
        for key, replacement in env.items():
            expanded = expanded.replace(f"${key}", replacement)
            expanded = expanded.replace(f"${{{key}}}", replacement)
    return Path(os.path.expandvars(expanded)).expanduser()


def build_config(micsync_home: Path, env: Mapping[str, str]) -> Config:
    path_env = {"HOME": str(Path.home()), "MICSYNC_HOME": str(micsync_home), **env}
    runtime_root = _expand_path(
        env.get("MICSYNC_RUNTIME_ROOT", str(micsync_home / "runtime")),
        path_env,
    )
    recordings_root = _expand_path(
        env.get("MICSYNC_RECORDINGS_ROOT", str(micsync_home / "recordings")),
        path_env,
    )
    recordings_db_path = _expand_path(
        env.get(
            "MICSYNC_RECORDINGS_DB_PATH",
            str(recordings_root / "db" / "recordings.sqlite3"),
        ),
        path_env,
    )
    extension_allowlist = tuple(
        part.strip()
        for part in env.get("MICSYNC_EXTENSION_ALLOWLIST", ".wav").split(",")
        if part.strip()
    )
    return Config(
        runtime_root=runtime_root,
        recordings_root=recordings_root,
        recordings_raw_root=recordings_root / "raw",
        recordings_derived_root=recordings_root / "organized",
        recordings_db_path=recordings_db_path,
        recordings_tmp_root=recordings_root / "tmp",
        max_file_size_mb=_coerce_optional_int(env.get("MICSYNC_MAX_FILE_SIZE_MB")),
        extension_allowlist=extension_allowlist,
        variant_policy=env.get("MICSYNC_VARIANT_POLICY", "all"),
        enable_derived_outputs=_coerce_bool(
            env.get("MICSYNC_ENABLE_DERIVED_OUTPUTS"),
            True,
        ),
        derived_outputs_strategy=env.get(
            "MICSYNC_DERIVED_OUTPUTS_STRATEGY",
            "auto",
        ),
        organized_layout=env.get("MICSYNC_ORGANIZED_LAYOUT", "timeline"),
        segment_cadence_seconds=int(env.get("MICSYNC_SEGMENT_CADENCE_SECONDS", "1800")),
        segment_group_tolerance_ms=int(
            env.get("MICSYNC_SEGMENT_GROUP_TOLERANCE_MS", "1000")
        ),
        stale_lock_timeout_seconds=int(
            env.get("MICSYNC_STALE_LOCK_TIMEOUT_SECONDS", "300")
        ),
        notify=_coerce_bool(env.get("MICSYNC_NOTIFY"), True),
        eject=_coerce_bool(env.get("MICSYNC_EJECT"), True),
    )


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def apply_runtime_overrides(
    config: Config,
    *,
    max_file_size_mb: int | None,
    notify: bool | None,
    eject: bool | None,
    derived: bool | None,
) -> Config:
    return replace(
        config,
        max_file_size_mb=config.max_file_size_mb if max_file_size_mb is None else max_file_size_mb,
        enable_derived_outputs=(
            config.enable_derived_outputs if derived is None else derived
        ),
        notify=config.notify if notify is None else notify,
        eject=config.eject if eject is None else eject,
    )
