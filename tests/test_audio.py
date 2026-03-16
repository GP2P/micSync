import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from micsync.audio import derive_end_time, materialize_derived_file
from micsync.scanner import scan_candidates, should_include_file


class AudioTest(unittest.TestCase):
    def test_end_time_uses_duration_when_available(self) -> None:
        end_time = derive_end_time("2026-06-08T11:20:48", duration_ms=30000)
        self.assertEqual(end_time, "2026-06-08T11:21:18")

    def test_max_size_filter_excludes_large_file(self) -> None:
        self.assertFalse(
            should_include_file(
                path=Path("TX02_MIC001_20260608_112048_orig.wav"),
                file_size_bytes=11 * 1024 * 1024,
                allow_extensions={".wav"},
                max_file_size_mb=10,
            )
        )

    def test_scan_candidates_skips_excluded_volumes_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            volumes_root = Path(tmpdir)
            included_volume = volumes_root / "MIC 01"
            excluded_volume = volumes_root / "Macintosh HD"
            included_volume.mkdir()
            excluded_volume.mkdir()
            (included_volume / "TX_MIC001_20260308_143058").mkdir()
            (included_volume / "TX_MIC001_20260308_143058" / "TX02_MIC001_20260608_112048_orig.wav").write_bytes(b"test")
            (excluded_volume / "TX_MIC001_20260308_143058").mkdir()
            (excluded_volume / "TX_MIC001_20260308_143058" / "TX02_MIC001_20260608_112048_orig.wav").write_bytes(b"skip")

            candidates = scan_candidates(
                allow_extensions={".wav"},
                max_file_size_mb=10,
                volumes_root=volumes_root,
                exclude_volume_labels={"Macintosh HD"},
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].volume_label, "MIC 01")

    def test_scan_candidates_can_limit_to_explicit_volume_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            volumes_root = Path(tmpdir)
            included_volume = volumes_root / "MIC 01"
            skipped_volume = volumes_root / "MIC 02"
            included_volume.mkdir()
            skipped_volume.mkdir()
            (included_volume / "TX_MIC001_20260308_143058").mkdir()
            (included_volume / "TX_MIC001_20260308_143058" / "TX02_MIC001_20260608_112048_orig.wav").write_bytes(b"one")
            (skipped_volume / "TX_MIC001_20260308_143058").mkdir()
            (skipped_volume / "TX_MIC001_20260308_143058" / "TX02_MIC002_20260608_112048_orig.wav").write_bytes(b"two")

            candidates = scan_candidates(
                allow_extensions={".wav"},
                max_file_size_mb=10,
                include_volume_roots=[included_volume],
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].volume_root, included_volume)

    def test_scan_candidates_only_descends_into_tx_mic_folders_on_volume_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            volumes_root = Path(tmpdir)
            volume = volumes_root / "MIC 01"
            volume.mkdir()
            valid_dir = volume / "TX_MIC001_20260308_143058"
            trash_dir = volume / ".Trashes"
            valid_dir.mkdir()
            trash_dir.mkdir()
            (valid_dir / "TX02_MIC001_20260608_112048_orig.wav").write_bytes(b"keep")
            (trash_dir / "TX_MIC999_20260308_143058").mkdir()
            (
                trash_dir
                / "TX_MIC999_20260308_143058"
                / "TX02_MIC999_20260608_112048_orig.wav"
            ).write_bytes(b"skip")

            candidates = scan_candidates(
                allow_extensions={".wav"},
                max_file_size_mb=10,
                volumes_root=volumes_root,
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].source_parent_folder, "TX_MIC001_20260308_143058")

    def test_materialize_derived_file_falls_back_to_copy_when_clone_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "raw.wav"
            dest_path = root / "derived" / "normalized.wav"
            source_path.write_bytes(b"audio-bytes")

            with patch("micsync.audio.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 1
                result = materialize_derived_file(
                    source_path=source_path,
                    dest_path=dest_path,
                    strategy="clone_then_copy",
                )

            self.assertEqual(result, dest_path)
            self.assertEqual(dest_path.read_bytes(), b"audio-bytes")
