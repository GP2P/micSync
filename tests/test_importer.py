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

            row = catalog.fetch_recording_file(outcome.file_id)
            self.assertEqual(row["source_volume_label"], "MIC 01")
            self.assertEqual(row["source_volume_identifier"], "MIC 01")
            self.assertIsNotNone(row["first_seen_at"])
            self.assertIsNotNone(row["last_attempted_at"])
            self.assertIsNotNone(row["completed_at"])
