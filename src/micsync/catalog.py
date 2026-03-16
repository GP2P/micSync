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
                create table if not exists takes (
                    id integer primary key,
                    take_key text not null unique,
                    take_start_at text not null,
                    take_end_at text,
                    tx_slot text not null,
                    physical_mic_id integer not null default 0,
                    source_parent_folder text,
                    first_imported_at text not null default (datetime('now')),
                    last_updated_at text not null default (datetime('now')),
                    health_status text not null default 'ok'
                );

                create table if not exists segments (
                    id integer primary key,
                    take_id integer not null references takes(id),
                    segment_key text not null unique,
                    segment_index integer,
                    segment_start_at text not null,
                    segment_end_at text,
                    tx_slot text not null,
                    mic_sequence text not null,
                    physical_mic_id integer not null default 0,
                    source_parent_folder text,
                    duration_ms integer,
                    first_seen_at text,
                    last_attempted_at text,
                    completed_at text,
                    health_status text not null default 'ok',
                    anomaly_code text,
                    anomaly_detail text,
                    last_updated_at text not null default (datetime('now'))
                );

                create table if not exists artifacts (
                    id integer primary key,
                    take_id integer not null references takes(id),
                    segment_id integer not null references segments(id),
                    segment_key text not null,
                    run_id text,
                    source_volume_label text,
                    source_volume_identifier text,
                    source_mount_path text,
                    source_parent_folder text,
                    source_filename text,
                    source_relative_path text,
                    source_size_bytes integer,
                    source_checksum text,
                    artifact_start_at text,
                    artifact_end_at text,
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

    def upsert_take(
        self,
        *,
        take_key: str,
        take_start_at: str,
        take_end_at: str | None,
        tx_slot: str,
        physical_mic_id: int,
        source_parent_folder: str,
        health_status: str = "ok",
    ) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                insert into takes (
                    take_key,
                    take_start_at,
                    take_end_at,
                    tx_slot,
                    physical_mic_id,
                    source_parent_folder,
                    health_status
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(take_key) do update set
                    take_start_at=excluded.take_start_at,
                    take_end_at=coalesce(excluded.take_end_at, takes.take_end_at),
                    tx_slot=excluded.tx_slot,
                    physical_mic_id=excluded.physical_mic_id,
                    source_parent_folder=excluded.source_parent_folder,
                    health_status=case
                        when excluded.health_status != 'ok' then excluded.health_status
                        else takes.health_status
                    end,
                    last_updated_at=datetime('now')
                """,
                (
                    take_key,
                    take_start_at,
                    take_end_at,
                    tx_slot,
                    physical_mic_id,
                    source_parent_folder,
                    health_status,
                ),
            )
            row = conn.execute(
                "select id from takes where take_key = ?",
                (take_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("take upsert failed")
            return int(row["id"])

    def upsert_segment(
        self,
        *,
        take_id: int,
        segment_key: str,
        segment_start_at: str,
        segment_end_at: str | None,
        tx_slot: str,
        mic_sequence: str,
        physical_mic_id: int,
        source_parent_folder: str,
        duration_ms: int | None,
        first_seen_at: str,
        last_attempted_at: str,
        completed_at: str | None,
        health_status: str = "ok",
        anomaly_code: str | None = None,
        anomaly_detail: str | None = None,
    ) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                insert into segments (
                    take_id,
                    segment_key,
                    segment_start_at,
                    segment_end_at,
                    tx_slot,
                    mic_sequence,
                    physical_mic_id,
                    source_parent_folder,
                    duration_ms,
                    first_seen_at,
                    last_attempted_at,
                    completed_at,
                    health_status,
                    anomaly_code,
                    anomaly_detail
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(segment_key) do update set
                    take_id=excluded.take_id,
                    segment_start_at=excluded.segment_start_at,
                    segment_end_at=coalesce(excluded.segment_end_at, segments.segment_end_at),
                    tx_slot=excluded.tx_slot,
                    mic_sequence=excluded.mic_sequence,
                    physical_mic_id=excluded.physical_mic_id,
                    source_parent_folder=excluded.source_parent_folder,
                    duration_ms=coalesce(excluded.duration_ms, segments.duration_ms),
                    first_seen_at=coalesce(segments.first_seen_at, excluded.first_seen_at),
                    last_attempted_at=excluded.last_attempted_at,
                    completed_at=coalesce(excluded.completed_at, segments.completed_at),
                    health_status=case
                        when excluded.health_status != 'ok' then excluded.health_status
                        else segments.health_status
                    end,
                    anomaly_code=coalesce(excluded.anomaly_code, segments.anomaly_code),
                    anomaly_detail=coalesce(excluded.anomaly_detail, segments.anomaly_detail),
                    last_updated_at=datetime('now')
                """,
                (
                    take_id,
                    segment_key,
                    segment_start_at,
                    segment_end_at,
                    tx_slot,
                    mic_sequence,
                    physical_mic_id,
                    source_parent_folder,
                    duration_ms,
                    first_seen_at,
                    last_attempted_at,
                    completed_at,
                    health_status,
                    anomaly_code,
                    anomaly_detail,
                ),
            )
            row = conn.execute(
                "select id from segments where segment_key = ?",
                (segment_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("segment upsert failed")
            return int(row["id"])

    def insert_artifact(self, **fields: Any) -> int:
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        values = tuple(fields.values())
        with self._connect() as conn:
            cursor = conn.execute(
                f"insert into artifacts ({columns}) values ({placeholders})",
                values,
            )
            return int(cursor.lastrowid)

    def fetch_take(self, take_id: int) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "select * from takes where id = ?",
                (take_id,),
            ).fetchone()
            if row is None:
                raise KeyError(take_id)
            return row

    def fetch_segment(self, segment_id: int) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "select * from segments where id = ?",
                (segment_id,),
            ).fetchone()
            if row is None:
                raise KeyError(segment_id)
            return row

    def fetch_artifact(self, artifact_id: int) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "select * from artifacts where id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(artifact_id)
            return row

    def count_rows(self, table_name: str) -> int:
        with self._connect() as conn:
            row = conn.execute(f"select count(*) as count from {table_name}").fetchone()
            if row is None:
                return 0
            return int(row["count"])
