import unittest
from pathlib import Path
import tempfile

from micsync.audio import derive_end_time
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
