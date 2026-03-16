from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Iterator


class Catalog:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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

                create table if not exists source_files (
                    id integer primary key,
                    source_key text not null unique,
                    segment_id integer references segments(id),
                    source_volume_label text,
                    source_volume_identifier text,
                    source_mount_path text,
                    source_parent_folder text,
                    source_filename text,
                    source_relative_path text,
                    physical_mic_id integer not null default 0,
                    raw_relative_path text,
                    source_size_bytes integer,
                    source_checksum text,
                    recording_start_at text,
                    recording_end_at text,
                    duration_ms integer,
                    variant text,
                    mirror_status text,
                    first_seen_at text,
                    last_attempted_at text,
                    mirrored_at text,
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
                    take_start_at=case
                        when excluded.take_start_at < takes.take_start_at then excluded.take_start_at
                        else takes.take_start_at
                    end,
                    take_end_at=case
                        when takes.take_end_at is null then excluded.take_end_at
                        when excluded.take_end_at is null then takes.take_end_at
                        when excluded.take_end_at > takes.take_end_at then excluded.take_end_at
                        else takes.take_end_at
                    end,
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
        segment_index: int = 0,
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
                    segment_index,
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
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(segment_key) do update set
                    take_id=excluded.take_id,
                    segment_index=excluded.segment_index,
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
                    segment_index,
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

    def find_latest_segment_for_session(
        self,
        *,
        tx_slot: str,
        physical_mic_id: int,
        source_parent_folder: str,
        before_start_at: str,
    ) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                select
                    s.*,
                    t.take_key
                from segments s
                join takes t on t.id = s.take_id
                where s.tx_slot = ?
                  and s.physical_mic_id = ?
                  and coalesce(s.source_parent_folder, '') = coalesce(?, '')
                  and s.segment_start_at < ?
                order by s.segment_start_at desc
                limit 1
                """,
                (
                    tx_slot,
                    physical_mic_id,
                    source_parent_folder,
                    before_start_at,
                ),
            ).fetchone()

    def upsert_source_file(
        self,
        *,
        source_key: str,
        segment_id: int | None,
        source_volume_label: str | None,
        source_volume_identifier: str | None,
        source_mount_path: str | None,
        source_parent_folder: str | None,
        source_filename: str,
        source_relative_path: str,
        physical_mic_id: int,
        raw_relative_path: str,
        source_size_bytes: int,
        source_checksum: str,
        recording_start_at: str | None,
        recording_end_at: str | None,
        duration_ms: int | None,
        variant: str | None,
        mirror_status: str,
        first_seen_at: str | None,
        last_attempted_at: str | None,
        mirrored_at: str | None,
        error_phase: str | None,
        error_detail: str | None,
    ) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                insert into source_files (
                    source_key,
                    segment_id,
                    source_volume_label,
                    source_volume_identifier,
                    source_mount_path,
                    source_parent_folder,
                    source_filename,
                    source_relative_path,
                    physical_mic_id,
                    raw_relative_path,
                    source_size_bytes,
                    source_checksum,
                    recording_start_at,
                    recording_end_at,
                    duration_ms,
                    variant,
                    mirror_status,
                    first_seen_at,
                    last_attempted_at,
                    mirrored_at,
                    error_phase,
                    error_detail
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source_key) do update set
                    segment_id=coalesce(excluded.segment_id, source_files.segment_id),
                    source_volume_label=excluded.source_volume_label,
                    source_volume_identifier=excluded.source_volume_identifier,
                    source_mount_path=excluded.source_mount_path,
                    source_parent_folder=excluded.source_parent_folder,
                    source_filename=excluded.source_filename,
                    source_relative_path=excluded.source_relative_path,
                    physical_mic_id=excluded.physical_mic_id,
                    raw_relative_path=excluded.raw_relative_path,
                    source_size_bytes=excluded.source_size_bytes,
                    source_checksum=excluded.source_checksum,
                    recording_start_at=coalesce(excluded.recording_start_at, source_files.recording_start_at),
                    recording_end_at=coalesce(excluded.recording_end_at, source_files.recording_end_at),
                    duration_ms=coalesce(excluded.duration_ms, source_files.duration_ms),
                    variant=excluded.variant,
                    mirror_status=excluded.mirror_status,
                    first_seen_at=coalesce(source_files.first_seen_at, excluded.first_seen_at),
                    last_attempted_at=excluded.last_attempted_at,
                    mirrored_at=coalesce(excluded.mirrored_at, source_files.mirrored_at),
                    error_phase=excluded.error_phase,
                    error_detail=excluded.error_detail
                """,
                (
                    source_key,
                    segment_id,
                    source_volume_label,
                    source_volume_identifier,
                    source_mount_path,
                    source_parent_folder,
                    source_filename,
                    source_relative_path,
                    physical_mic_id,
                    raw_relative_path,
                    source_size_bytes,
                    source_checksum,
                    recording_start_at,
                    recording_end_at,
                    duration_ms,
                    variant,
                    mirror_status,
                    first_seen_at,
                    last_attempted_at,
                    mirrored_at,
                    error_phase,
                    error_detail,
                ),
            )
            row = conn.execute(
                "select id from source_files where source_key = ?",
                (source_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("source file upsert failed")
            return int(row["id"])

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

    def fetch_source_file(self, source_file_id: int) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "select * from source_files where id = ?",
                (source_file_id,),
            ).fetchone()
            if row is None:
                raise KeyError(source_file_id)
            return row

    def fetch_pending_source_files_for_derivation(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select *
                from source_files
                where segment_id is null
                order by
                    case when recording_start_at is null then 1 else 0 end,
                    recording_start_at asc,
                    physical_mic_id asc,
                    coalesce(source_parent_folder, '') asc,
                    source_filename asc,
                    id asc
                """
            ).fetchall()
            return list(rows)

    def assign_source_file_to_segment(self, *, source_file_id: int, segment_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update source_files
                set segment_id = ?
                where id = ?
                """,
                (segment_id, source_file_id),
            )

    def count_rows(self, table_name: str) -> int:
        with self._connect() as conn:
            row = conn.execute(f"select count(*) as count from {table_name}").fetchone()
            if row is None:
                return 0
            return int(row["count"])
