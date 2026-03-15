import unittest
from pathlib import Path

from micsync.audio import derive_end_time
from micsync.scanner import should_include_file


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
