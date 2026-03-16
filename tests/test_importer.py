from datetime import datetime
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from micsync.catalog import Catalog
from micsync.importer import (
    derive_mirrored_recording,
    import_recording,
    mirror_recording_to_raw,
    plan_destination_path,
)


class ImporterTest(unittest.TestCase):
    def test_mirror_recording_to_raw_preserves_source_file_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_file.write_bytes(b"not-a-real-wav")
            source_created_at = datetime(2024, 1, 2, 3, 4, 5)
            source_modified_at = datetime(2024, 1, 2, 4, 5, 6)
            subprocess.run(
                [
                    "SetFile",
                    "-d",
                    source_created_at.strftime("%m/%d/%Y %H:%M:%S"),
                    str(source_file),
                ],
                check=True,
            )
            os.utime(
                source_file,
                (
                    source_modified_at.timestamp(),
                    source_modified_at.timestamp(),
                ),
            )
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            outcome = mirror_recording_to_raw(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
            )

            source_stat = source_file.stat()
            mirrored_stat = outcome.raw_path.stat()
            self.assertEqual(int(mirrored_stat.st_mtime), int(source_stat.st_mtime))
            self.assertEqual(int(mirrored_stat.st_birthtime), int(source_stat.st_birthtime))

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
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            outcome = import_recording(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-123",
            )

            take_row = catalog.fetch_take(outcome.take_id)
            segment_row = catalog.fetch_segment(outcome.segment_id)
            source_file_row = catalog.fetch_source_file(outcome.source_file_id)
            self.assertIsNotNone(take_row["first_imported_at"])
            self.assertEqual(segment_row["segment_key"], "20260608_112048_TX02_MIC001")
            self.assertEqual(source_file_row["source_volume_label"], "MIC 01")
            self.assertEqual(source_file_row["source_volume_identifier"], "MIC 01")
            self.assertIsNotNone(source_file_row["first_seen_at"])
            self.assertIsNotNone(source_file_row["last_attempted_at"])
            self.assertIsNotNone(source_file_row["mirrored_at"])
            self.assertEqual(
                source_file_row["raw_relative_path"],
                "raw/MIC_01/TX_MIC001_20260308_143058/TX02_MIC001_20260608_112048_orig.wav",
            )
            self.assertEqual(
                outcome.raw_path,
                root
                / "recordings"
                / "audio"
                / "raw"
                / "MIC_01"
                / "TX_MIC001_20260308_143058"
                / "TX02_MIC001_20260608_112048_orig.wav",
            )

    def test_mirror_recording_to_raw_creates_source_file_without_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_file.write_bytes(b"not-a-real-wav")
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            outcome = mirror_recording_to_raw(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
            )

            source_file_row = catalog.fetch_source_file(outcome.source_file_id)
            self.assertEqual(outcome.status, "mirrored")
            self.assertTrue(outcome.raw_path.exists())
            self.assertIsNone(source_file_row["segment_id"])
            self.assertEqual(source_file_row["variant"], "orig")

    def test_mirror_recording_to_raw_skips_copy_when_exact_duplicate_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_bytes = b"same-audio"
            source_file.write_bytes(source_bytes)
            existing_raw = (
                root
                / "recordings"
                / "audio"
                / "raw"
                / "MIC_01"
                / source_dir.name
                / source_file.name
            )
            existing_raw.parent.mkdir(parents=True, exist_ok=True)
            existing_raw.write_bytes(source_bytes)
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            with patch("micsync.importer._copy_with_checksum", side_effect=AssertionError("unexpected copy")):
                outcome = mirror_recording_to_raw(
                    source_path=source_file,
                    source_mount_path=source_mount,
                    source_parent_folder=source_dir.name,
                    volume_label="MIC 01",
                    recordings_root=root / "recordings" / "audio",
                    tmp_root=root / "recordings" / "audio" / "tmp",
                    catalog=catalog,
                    log_path=root / "micSync" / "logs" / "runs.log",
                )

            self.assertEqual(outcome.status, "duplicate")
            self.assertEqual(outcome.raw_path, existing_raw)
            self.assertFalse((root / "recordings" / "audio" / "tmp").exists())

    def test_derive_mirrored_recording_assigns_take_and_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_file.write_bytes(b"not-a-real-wav")
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            mirrored = mirror_recording_to_raw(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
            )

            derived = derive_mirrored_recording(
                raw_path=mirrored.raw_path,
                source_file_id=mirrored.source_file_id,
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                segment_cadence_seconds=1800,
                segment_group_tolerance_ms=1000,
            )

            source_file_row = catalog.fetch_source_file(mirrored.source_file_id)
            self.assertEqual(derived.source_file_id, mirrored.source_file_id)
            self.assertIsNotNone(source_file_row["segment_id"])
            self.assertEqual(catalog.count_rows("takes"), 1)
            self.assertEqual(catalog.count_rows("segments"), 1)

    def test_derive_mirrored_recording_can_create_optional_derived_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_file.write_bytes(b"not-a-real-wav")
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            mirrored = mirror_recording_to_raw(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
            )

            derived = derive_mirrored_recording(
                raw_path=mirrored.raw_path,
                source_file_id=mirrored.source_file_id,
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                enable_derived_outputs=True,
                derived_root=root / "recordings" / "audio" / "derived",
                derived_outputs_strategy="copy_only",
                segment_cadence_seconds=1800,
                segment_group_tolerance_ms=1000,
            )

            self.assertEqual(
                derived.derived_path,
                root
                / "recordings"
                / "audio"
                / "derived"
                / "normalized"
                / "2026"
                / "06"
                / "08"
                / "20260608_112048_TX02_MIC001_orig.wav",
            )
            self.assertTrue(derived.derived_path.exists())

    def test_hidden_source_derives_hidden_metadata_without_normalized_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / ".Trashes" / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX02_MIC001_20260608_112048_orig.wav"
            source_file.write_bytes(b"not-a-real-wav")
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            mirrored = mirror_recording_to_raw(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder="TX_MIC001_20260308_143058",
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                hidden=True,
            )

            derived = derive_mirrored_recording(
                raw_path=mirrored.raw_path,
                source_file_id=mirrored.source_file_id,
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                enable_derived_outputs=True,
                derived_root=root / "recordings" / "audio" / "derived",
                derived_outputs_strategy="copy_only",
                segment_cadence_seconds=1800,
                segment_group_tolerance_ms=1000,
            )

            source_file_row = catalog.fetch_source_file(mirrored.source_file_id)
            segment_row = catalog.fetch_segment(derived.segment_id)
            take_row = catalog.fetch_take(derived.take_id)
            self.assertEqual(source_file_row["hidden"], 1)
            self.assertEqual(segment_row["hidden"], 1)
            self.assertEqual(take_row["hidden"], 1)
            self.assertIsNone(derived.derived_path)

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
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            orig = import_recording(
                source_path=source_orig,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-200",
            )
            edit = import_recording(
                source_path=source_edit,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-200",
            )

            self.assertEqual(orig.take_id, edit.take_id)
            self.assertEqual(orig.segment_id, edit.segment_id)
            self.assertEqual(catalog.count_rows("takes"), 1)
            self.assertEqual(catalog.count_rows("segments"), 1)
            self.assertEqual(catalog.count_rows("source_files"), 2)

    def test_zero_byte_recording_is_flagged_with_warning_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 01"
            source_dir = source_mount / "TX_MIC001_20260308_143058"
            source_dir.mkdir(parents=True)
            source_file = source_dir / "TX01_MIC029_20260312_175545_orig.wav"
            source_file.write_bytes(b"")
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            outcome = import_recording(
                source_path=source_file,
                source_mount_path=source_mount,
                source_parent_folder=source_dir.name,
                volume_label="MIC 01",
                recordings_root=root / "recordings" / "audio",
                tmp_root=root / "recordings" / "audio" / "tmp",
                catalog=catalog,
                log_path=root / "micSync" / "logs" / "runs.log",
                run_id="run-124",
            )

            segment_row = catalog.fetch_segment(outcome.segment_id)
            source_file_row = catalog.fetch_source_file(outcome.source_file_id)
            self.assertEqual(outcome.warning_count, 1)
            self.assertEqual(segment_row["anomaly_code"], "zero_byte_source")
            self.assertIn("zero-byte", segment_row["anomaly_detail"])
            self.assertEqual(source_file_row["error_phase"], "source_validation")
            self.assertIn("zero-byte", source_file_row["error_detail"])

    def test_contiguous_full_segments_share_same_take(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 02"
            source_dir = source_mount / "TX_MIC001_20260311_195412"
            source_dir.mkdir(parents=True)
            first_file = source_dir / "TX02_MIC028_20260311_195412_orig.wav"
            second_file = source_dir / "TX02_MIC029_20260311_202412_orig.wav"
            first_file.write_bytes(b"first")
            second_file.write_bytes(b"second")
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            with patch(
                "micsync.importer.read_duration_ms",
                side_effect=[1800045, 1800045],
            ):
                first = import_recording(
                    source_path=first_file,
                    source_mount_path=source_mount,
                    source_parent_folder=source_dir.name,
                    volume_label="MIC 02",
                    recordings_root=root / "recordings" / "audio",
                    tmp_root=root / "recordings" / "audio" / "tmp",
                    catalog=catalog,
                    log_path=root / "micSync" / "logs" / "runs.log",
                    run_id="run-300",
                    segment_cadence_seconds=1800,
                    segment_group_tolerance_ms=1000,
                )
                second = import_recording(
                    source_path=second_file,
                    source_mount_path=source_mount,
                    source_parent_folder=source_dir.name,
                    volume_label="MIC 02",
                    recordings_root=root / "recordings" / "audio",
                    tmp_root=root / "recordings" / "audio" / "tmp",
                    catalog=catalog,
                    log_path=root / "micSync" / "logs" / "runs.log",
                    run_id="run-300",
                    segment_cadence_seconds=1800,
                    segment_group_tolerance_ms=1000,
                )

            take_row = catalog.fetch_take(first.take_id)
            second_segment_row = catalog.fetch_segment(second.segment_id)
            self.assertEqual(first.take_id, second.take_id)
            self.assertNotEqual(first.segment_id, second.segment_id)
            self.assertEqual(catalog.count_rows("takes"), 1)
            self.assertEqual(catalog.count_rows("segments"), 2)
            self.assertEqual(take_row["take_start_at"], "2026-03-11T19:54:12")
            self.assertEqual(take_row["take_end_at"], "2026-03-11T20:54:12")
            self.assertEqual(second_segment_row["segment_index"], 1)

    def test_grouping_tolerance_can_force_new_take(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_mount = root / "MIC 02"
            source_dir = source_mount / "TX_MIC001_20260311_195412"
            source_dir.mkdir(parents=True)
            first_file = source_dir / "TX02_MIC028_20260311_195412_orig.wav"
            second_file = source_dir / "TX02_MIC029_20260311_202412_orig.wav"
            first_file.write_bytes(b"first")
            second_file.write_bytes(b"second")
            catalog = Catalog(root / "recordings" / "audio" / "db" / "recordings.sqlite3")

            with patch(
                "micsync.importer.read_duration_ms",
                side_effect=[1800045, 1800045],
            ):
                first = import_recording(
                    source_path=first_file,
                    source_mount_path=source_mount,
                    source_parent_folder=source_dir.name,
                    volume_label="MIC 02",
                    recordings_root=root / "recordings" / "audio",
                    tmp_root=root / "recordings" / "audio" / "tmp",
                    catalog=catalog,
                    log_path=root / "micSync" / "logs" / "runs.log",
                    run_id="run-301",
                    segment_cadence_seconds=1800,
                    segment_group_tolerance_ms=0,
                )
                second = import_recording(
                    source_path=second_file,
                    source_mount_path=source_mount,
                    source_parent_folder=source_dir.name,
                    volume_label="MIC 02",
                    recordings_root=root / "recordings" / "audio",
                    tmp_root=root / "recordings" / "audio" / "tmp",
                    catalog=catalog,
                    log_path=root / "micSync" / "logs" / "runs.log",
                    run_id="run-301",
                    segment_cadence_seconds=1800,
                    segment_group_tolerance_ms=0,
                )

            self.assertNotEqual(first.take_id, second.take_id)
            self.assertEqual(catalog.count_rows("takes"), 2)
