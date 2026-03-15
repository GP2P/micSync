import tempfile
import unittest
from pathlib import Path

from micsync.importer import plan_destination_path


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
