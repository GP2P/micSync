import unittest
from pathlib import Path
from unittest.mock import patch

from micsync.notify import (
    build_stop_command,
    build_completion_message,
    build_start_message,
    build_stopped_message,
    copy_to_clipboard,
)


class NotifyTest(unittest.TestCase):
    def test_start_message_can_include_stop_hint(self) -> None:
        message = build_start_message(
            candidate_count=3,
            total_bytes=1024,
            stop_hint="copied exact stop command to clipboard",
        )
        self.assertIn("3 candidate files", message)
        self.assertIn("stop:", message)

    def test_completion_message_includes_counts_and_elapsed_time(self) -> None:
        message = build_completion_message(
            imported_count=3,
            duplicate_count=1,
            failed_count=0,
            warning_count=2,
            total_bytes=1024,
            elapsed_seconds=12,
            ejected_volumes=["MIC 01", "MIC 02"],
        )
        self.assertIn("3 imported", message)
        self.assertIn("1 duplicate", message)
        self.assertIn("2 warning", message)
        self.assertIn("12s", message)

    def test_stopped_message_includes_summary(self) -> None:
        message = build_stopped_message(
            imported_count=2,
            duplicate_count=1,
            warning_count=1,
            total_bytes=2048,
            elapsed_seconds=7,
        )
        self.assertIn("stopped", message)
        self.assertIn("2 imported", message)
        self.assertIn("1 duplicate", message)
        self.assertIn("7s", message)

    def test_build_stop_command_uses_absolute_paths(self) -> None:
        command = build_stop_command(
            deploy_root=Path("/srv/micsync"),
            data_root=Path("/var/lib/micsync-data"),
        )
        self.assertIn("NEXUS_DEPLOY_ROOT=/srv/micsync", command)
        self.assertIn("NEXUS_DATA_ROOT=/var/lib/micsync-data", command)
        self.assertIn("/srv/micsync/scripts/micsync.sh --stop", command)

    @patch("micsync.notify.subprocess.run")
    def test_copy_to_clipboard_returns_true_on_success(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        self.assertTrue(copy_to_clipboard("hello"))
        mock_run.assert_called_once()
