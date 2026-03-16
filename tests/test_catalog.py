import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from micsync.catalog import Catalog


class CatalogTest(unittest.TestCase):
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
            self.assertIsNotNone(row["first_imported_at"])

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
