import tempfile
import unittest
from pathlib import Path

from micsync.catalog import Catalog
from micsync.importer import import_recording, plan_destination_path


class ImporterTest(unittest.TestCase):
    def test_conflicting_duplicate_gets_dup_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            existing = root / "audio/2026/06/08/20260608_112048_TX02_MIC001_orig.wav"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_bytes(b"old")
            planned = plan_destination_path(
                recordings_root=root,
                relative_dir=Path("audio/2026/06/08"),
                dest_name="20260608_112048_TX02_MIC001_orig.wav",
                incoming_checksum="new",
                existing_checksum_lookup=lambda _: "old",
            )
            self.assertEqual(planned.name, "20260608_112048_TX02_MIC001_orig_dup1.wav")

    def test_import_recording_populates_tracking_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_file.write_bytes(b"not-a-real-wav")
            catalog = Catalog(root / "recordings" / "db" / "recordings.sqlite3")

            outcome = import_recording(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings",
                tmp_root=root / "recordings" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-123",
            )

            take_row = catalog.fetch_take(outcome.take_id)
            segment_row = catalog.fetch_segment(outcome.segment_id)
            artifact_row = catalog.fetch_artifact(outcome.artifact_id)
            self.assertIsNotNone(take_row["first_imported_at"])
            self.assertEqual(segment_row["segment_key"], "20260608_112048_TX02_MIC001")
            self.assertEqual(artifact_row["source_volume_label"], "MIC 01")
            self.assertEqual(artifact_row["source_volume_identifier"], "MIC 01")
            self.assertIsNotNone(artifact_row["first_seen_at"])
            self.assertIsNotNone(artifact_row["last_attempted_at"])
            self.assertIsNotNone(artifact_row["completed_at"])

    def test_orig_and_edit_share_same_take_and_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_orig = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_edit = source_dir / "TX02_MIC001_20260608_112048_edit.wav"
            source_orig.write_bytes(b"orig-bytes")
            source_edit.write_bytes(b"edit-bytes")
            catalog = Catalog(root / "recordings" / "db" / "recordings.sqlite3")

            orig = import_recording(
                source_path=source_orig,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings",
                tmp_root=root / "recordings" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-200",
            )
            edit = import_recording(
                source_path=source_edit,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings",
                tmp_root=root / "recordings" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-200",
            )

            self.assertEqual(orig.take_id, edit.take_id)
            self.assertEqual(orig.segment_id, edit.segment_id)
            self.assertEqual(catalog.count_rows("takes"), 1)
            self.assertEqual(catalog.count_rows("segments"), 1)
            self.assertEqual(catalog.count_rows("artifacts"), 2)

    def test_zero_byte_recording_is_flagged_with_warning_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX01_MIC029_20260312_175545_orig.wav"
            source_file.write_bytes(b"")
            catalog = Catalog(root / "recordings" / "db" / "recordings.sqlite3")

            outcome = import_recording(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings",
                tmp_root=root / "recordings" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-124",
            )

            segment_row = catalog.fetch_segment(outcome.segment_id)
            artifact_row = catalog.fetch_artifact(outcome.artifact_id)
            self.assertEqual(outcome.warning_count, 1)
            self.assertEqual(segment_row["anomaly_code"], "zero_byte_source")
            self.assertIn("zero-byte", segment_row["anomaly_detail"])
            self.assertEqual(artifact_row["error_phase"], "source_validation")
            self.assertIn("zero-byte", artifact_row["error_detail"])
