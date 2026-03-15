from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class Catalog:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists recordings (
                    id integer primary key,
                    recording_group_key text not null unique,
                    recording_start_at text not null,
                    recording_end_at text,
                    tx_slot text not null,
                    mic_sequence text not null,
                    physical_mic_id integer not null default 0,
                    first_imported_at text,
                    last_updated_at text not null default (datetime('now'))
                );

                create table if not exists recording_files (
                    id integer primary key,
                    recording_id integer not null references recordings(id),
                    run_id text,
                    source_volume_label text,
                    source_volume_identifier text,
                    source_mount_path text,
                    source_parent_folder text,
                    source_filename text,
                    source_relative_path text,
                    source_size_bytes integer,
                    source_checksum text,
                    recording_start_at text,
                    recording_end_at text,
                    tx_slot text,
                    mic_sequence text,
                    variant text,
                    content_role text,
                    duration_ms integer,
                    physical_mic_id integer not null default 0,
                    dest_relative_path text,
                    dest_size_bytes integer,
                    import_status text,
                    first_seen_at text,
                    last_attempted_at text,
                    completed_at text,
                    duplicate_of integer,
                    error_phase text,
                    error_detail text
                );
                """
            )

    def upsert_recording(
        self,
        *,
        recording_group_key: str,
        recording_start_at: str,
        recording_end_at: str | None,
        tx_slot: str,
        mic_sequence: str,
        physical_mic_id: int,
    ) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                insert into recordings (
                    recording_group_key,
                    recording_start_at,
                    recording_end_at,
                    tx_slot,
                    mic_sequence,
                    physical_mic_id
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(recording_group_key) do update set
                    recording_start_at=excluded.recording_start_at,
                    recording_end_at=coalesce(excluded.recording_end_at, recordings.recording_end_at),
                    tx_slot=excluded.tx_slot,
                    mic_sequence=excluded.mic_sequence,
                    physical_mic_id=excluded.physical_mic_id,
                    last_updated_at=datetime('now')
                """,
                (
                    recording_group_key,
                    recording_start_at,
                    recording_end_at,
                    tx_slot,
                    mic_sequence,
                    physical_mic_id,
                ),
            )
            row = conn.execute(
                "select id from recordings where recording_group_key = ?",
                (recording_group_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("recording upsert failed")
            return int(row["id"])

    def insert_recording_file(self, **fields: Any) -> int:
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        values = tuple(fields.values())
        with self._connect() as conn:
            cursor = conn.execute(
                f"insert into recording_files ({columns}) values ({placeholders})",
                values,
            )
            return int(cursor.lastrowid)
