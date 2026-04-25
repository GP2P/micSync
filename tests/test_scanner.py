import tempfile
import unittest
from pathlib import Path
from unittest import mock

from micsync.scanner import scan_candidates


class ScannerTest(unittest.TestCase):
    def test_scan_reports_walk_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            volume = Path(tmpdir) / "MIC 01"
            volume.mkdir()
            errors = []

            def fake_walk(root, topdown, onerror):
                error = OSError("blocked")
                error.filename = str(Path(root) / "TX_MIC001_20260418_192303")
                onerror(error)
                return iter(())

            with mock.patch("micsync.scanner.os.walk", side_effect=fake_walk):
                candidates = scan_candidates(
                    allow_extensions={".wav"},
                    max_file_size_mb=None,
                    include_volume_roots=[volume],
                    on_scan_error=lambda path, error: errors.append((path, error)),
                )

        self.assertEqual(candidates, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], volume / "TX_MIC001_20260418_192303")


if __name__ == "__main__":
    unittest.main()
