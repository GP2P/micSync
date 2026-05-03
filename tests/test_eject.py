import unittest
from pathlib import Path
from unittest.mock import patch

from micsync.eject import eject_volume


class EjectTest(unittest.TestCase):
    @patch("micsync.eject.subprocess.run", side_effect=FileNotFoundError)
    def test_eject_volume_reports_missing_diskutil(self, mock_run) -> None:
        result = eject_volume(Path("/Volumes/MIC 01"))

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "diskutil is unavailable")
        mock_run.assert_called_once()
