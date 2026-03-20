from datetime import datetime
import tempfile
import unittest
from pathlib import Path
import sqlite3
from unittest.mock import patch

from micsync.catalog import Catalog


class CatalogTest(unittest.TestCase):
    def assert_is_offset_datetime(self, value: str | None) -> None:
        self.assertIsNotNone(value)
        parsed = datetime.fromisoformat(str(value))
        self.assertIsNotNone(parsed.tzinfo)
        self.assertIsNotNone(parsed.utcoffset())

    def test_connect_context_closes_connection(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.row_factory = None
                self.closed = False
                self.commit_called = False
                self.rollback_called = False

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def commit(self) -> None:
                self.commit_called = True

            def rollback(self) -> None:
                self.rollback_called = True

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            fake_connection = FakeConnection()

            with patch("micsync.catalog.sqlite3.connect", return_value=fake_connection):
                with catalog._connect() as conn:
                    self.assertIs(conn, fake_connection)

            self.assertTrue(fake_connection.commit_called)
            self.assertFalse(fake_connection.rollback_called)
            self.assertTrue(fake_connection.closed)

    def test_initialize_adds_hidden_columns_to_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    create table takes (
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

                    create table segments (
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

                    create table source_files (
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
                connection.commit()
            finally:
                connection.close()

            catalog = Catalog(db_path)
            with catalog._connect() as conn:
                take_columns = {row["name"] for row in conn.execute("pragma table_info(takes)").fetchall()}
                segment_columns = {row["name"] for row in conn.execute("pragma table_info(segments)").fetchall()}
                source_columns = {row["name"] for row in conn.execute("pragma table_info(source_files)").fetchall()}

            self.assertIn("hidden", take_columns)
            self.assertIn("hidden", segment_columns)
            self.assertIn("hidden", source_columns)

    def test_initialize_rebuilds_legacy_anomalies_table_without_lifecycle_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    create table anomalies (
                        id integer primary key,
                        run_id text not null,
                        phase text not null,
                        severity text not null,
                        code text not null,
                        message text not null,
                        source_file_id integer,
                        raw_relative_path text,
                        volume_label text,
                        created_at text not null default (datetime('now')),
                        acknowledged_at text,
                        resolved_at text
                    );

                    insert into anomalies (
                        id,
                        run_id,
                        phase,
                        severity,
                        code,
                        message,
                        source_file_id,
                        raw_relative_path,
                        volume_label,
                        created_at,
                        acknowledged_at,
                        resolved_at
                    ) values (
                        1,
                        'run-legacy',
                        'mirror',
                        'fail',
                        'mirror_failed',
                        'legacy failure',
                        7,
                        'raw/MIC_01/A/file.wav',
                        'MIC 01',
                        '2026-03-20 02:41:52',
                        null,
                        null
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

            catalog = Catalog(db_path)
            with catalog._connect() as conn:
                anomaly_columns = {
                    row["name"] for row in conn.execute("pragma table_info(anomalies)").fetchall()
                }
                rows = conn.execute("select * from anomalies").fetchall()

        self.assertNotIn("acknowledged_at", anomaly_columns)
        self.assertNotIn("resolved_at", anomaly_columns)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "run-legacy")
        self.assertEqual(rows[0]["created_at"], "2026-03-20 02:41:52")

    def test_upsert_take_creates_one_take_and_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            take_id_1 = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
            )
            take_id_2 = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
            )
            self.assertEqual(take_id_1, take_id_2)
            row = catalog.fetch_take(take_id_1)
            self.assert_is_offset_datetime(row["first_imported_at"])
            self.assert_is_offset_datetime(row["last_updated_at"])

    def test_source_files_attach_to_segments_and_preserve_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            take_id = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
            )
            segment_id = catalog.upsert_segment(
                take_id=take_id,
                segment_key="20260608_112048_TX02_MIC001",
                segment_start_at="2026-06-08T11:20:48",
                segment_end_at=None,
                tx_slot="TX02",
                mic_sequence="MIC001",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
                duration_ms=None,
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                completed_at="2026-06-08T11:21:00",
            )
            source_file_id = catalog.upsert_source_file(
                source_key="MIC 01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                segment_id=segment_id,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="TX_MIC001_20260308_143058",
                source_filename="TX02_MIC001_20260608_112048_orig.wav",
                source_relative_path="TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                source_size_bytes=123,
                source_checksum="abc123",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                mirrored_at="2026-06-08T11:21:02",
                error_phase=None,
                error_detail=None,
            )
            row = catalog.fetch_source_file(source_file_id)
            self.assertEqual(row["segment_id"], segment_id)
            self.assertEqual(row["variant"], "orig")
            self.assertEqual(row["raw_relative_path"], "raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav")

    def test_source_files_can_exist_before_segment_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)

            source_file_id = catalog.upsert_source_file(
                source_key="MIC 01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="TX_MIC001_20260308_143058",
                source_filename="TX02_MIC001_20260608_112048_orig.wav",
                source_relative_path="TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                source_size_bytes=123,
                source_checksum="abc123",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                mirrored_at="2026-06-08T11:21:02",
                error_phase=None,
                error_detail=None,
            )

            row = catalog.fetch_source_file(source_file_id)
            self.assertIsNone(row["segment_id"])

    def test_insert_anomaly_persists_event_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)

            anomaly_id = catalog.insert_anomaly(
                run_id="run-123",
                phase="derive",
                severity="fail",
                code="derive_failed",
                message="derive failed for raw/MIC_01/A/file.wav",
                source_file_id=7,
                raw_relative_path="raw/MIC_01/A/file.wav",
                volume_label="MIC 01",
            )

            rows = catalog.fetch_anomalies()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], anomaly_id)
        self.assertEqual(rows[0]["run_id"], "run-123")
        self.assertEqual(rows[0]["severity"], "fail")
        self.assertEqual(rows[0]["code"], "derive_failed")
        self.assert_is_offset_datetime(rows[0]["created_at"])

    def test_pending_source_files_for_derivation_are_ordered_by_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)

            earliest_id = catalog.upsert_source_file(
                source_key="raw/MIC_01/A/TX01_MIC001_20260308_142705_orig.wav",
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="A",
                source_filename="TX01_MIC001_20260308_142705_orig.wav",
                source_relative_path="A/TX01_MIC001_20260308_142705_orig.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/A/TX01_MIC001_20260308_142705_orig.wav",
                source_size_bytes=123,
                source_checksum="checksum-1",
                recording_start_at="2026-03-08T14:27:05",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-03-08T14:28:00",
                last_attempted_at="2026-03-08T14:28:00",
                mirrored_at="2026-03-08T14:28:00",
                error_phase=None,
                error_detail=None,
            )
            latest_id = catalog.upsert_source_file(
                source_key="raw/MIC_01/A/TX01_MIC003_20260308_143556_orig.wav",
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="A",
                source_filename="TX01_MIC003_20260308_143556_orig.wav",
                source_relative_path="A/TX01_MIC003_20260308_143556_orig.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/A/TX01_MIC003_20260308_143556_orig.wav",
                source_size_bytes=123,
                source_checksum="checksum-3",
                recording_start_at="2026-03-08T14:35:56",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-03-08T14:36:00",
                last_attempted_at="2026-03-08T14:36:00",
                mirrored_at="2026-03-08T14:36:00",
                error_phase=None,
                error_detail=None,
            )
            middle_id = catalog.upsert_source_file(
                source_key="raw/MIC_01/A/TX01_MIC002_20260308_143543_orig.wav",
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="A",
                source_filename="TX01_MIC002_20260308_143543_orig.wav",
                source_relative_path="A/TX01_MIC002_20260308_143543_orig.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/A/TX01_MIC002_20260308_143543_orig.wav",
                source_size_bytes=123,
                source_checksum="checksum-2",
                recording_start_at="2026-03-08T14:35:43",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-03-08T14:36:00",
                last_attempted_at="2026-03-08T14:36:00",
                mirrored_at="2026-03-08T14:36:00",
                error_phase=None,
                error_detail=None,
            )

            ordered_rows = catalog.fetch_pending_source_files_for_derivation()

            self.assertEqual(
                [int(row["id"]) for row in ordered_rows],
                [earliest_id, middle_id, latest_id],
            )

    def test_repeated_duplicate_upsert_preserves_existing_segment_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            take_id = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
            )
            segment_id = catalog.upsert_segment(
                take_id=take_id,
                segment_key="20260608_112048_TX02_MIC001",
                segment_start_at="2026-06-08T11:20:48",
                segment_end_at=None,
                tx_slot="TX02",
                mic_sequence="MIC001",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
                duration_ms=None,
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                completed_at="2026-06-08T11:21:00",
            )
            source_key = "raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav"
            source_file_id = catalog.upsert_source_file(
                source_key=source_key,
                segment_id=segment_id,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="TX_MIC001_20260308_143058",
                source_filename="TX02_MIC001_20260608_112048_orig.wav",
                source_relative_path="TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                physical_mic_id=1,
                raw_relative_path=source_key,
                source_size_bytes=123,
                source_checksum="abc123",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                mirrored_at="2026-06-08T11:21:02",
                error_phase=None,
                error_detail=None,
            )

            repeated_id = catalog.upsert_source_file(
                source_key=source_key,
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="TX_MIC001_20260308_143058",
                source_filename="TX02_MIC001_20260608_112048_orig.wav",
                source_relative_path="TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                physical_mic_id=1,
                raw_relative_path=source_key,
                source_size_bytes=123,
                source_checksum="abc123",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="duplicate",
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:30:00",
                mirrored_at="2026-06-08T11:21:02",
                error_phase=None,
                error_detail=None,
            )

            self.assertEqual(repeated_id, source_file_id)
            row = catalog.fetch_source_file(source_file_id)
            self.assertEqual(row["segment_id"], segment_id)
            self.assertEqual(row["mirror_status"], "duplicate")
            self.assertEqual(row["last_attempted_at"], "2026-06-08T11:30:00")
            self.assertEqual(catalog.fetch_pending_source_files_for_derivation(), [])

    def test_visible_source_keeps_segment_and_take_visible_when_hidden_variant_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            take_id = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
                hidden=True,
            )
            segment_id = catalog.upsert_segment(
                take_id=take_id,
                segment_key="20260608_112048_TX02_MIC001",
                segment_start_at="2026-06-08T11:20:48",
                segment_end_at=None,
                tx_slot="TX02",
                mic_sequence="MIC001",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
                duration_ms=None,
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                completed_at="2026-06-08T11:21:00",
                hidden=True,
            )
            hidden_source_id = catalog.upsert_source_file(
                source_key="raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_edit.wav",
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="TX_MIC001_20260308_143058",
                source_filename="TX02_MIC001_20260608_112048_edit.wav",
                source_relative_path="TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_edit.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_edit.wav",
                source_size_bytes=123,
                source_checksum="hidden",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                duration_ms=None,
                variant="edit",
                mirror_status="mirrored",
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                mirrored_at="2026-06-08T11:21:02",
                error_phase=None,
                error_detail=None,
                hidden=True,
            )
            visible_source_id = catalog.upsert_source_file(
                source_key="raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path="/Volumes/MIC 01",
                source_parent_folder="TX_MIC001_20260308_143058",
                source_filename="TX02_MIC001_20260608_112048_orig.wav",
                source_relative_path="TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
                source_size_bytes=123,
                source_checksum="visible",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                duration_ms=None,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                mirrored_at="2026-06-08T11:21:02",
                error_phase=None,
                error_detail=None,
                hidden=False,
            )

            catalog.assign_source_file_to_segment(source_file_id=hidden_source_id, segment_id=segment_id)
            catalog.assign_source_file_to_segment(source_file_id=visible_source_id, segment_id=segment_id)

            segment_row = catalog.fetch_segment(segment_id)
            take_row = catalog.fetch_take(take_id)
            self.assertEqual(segment_row["hidden"], 0)
            self.assertEqual(take_row["hidden"], 0)
