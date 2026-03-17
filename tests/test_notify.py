import unittest
from pathlib import Path
from unittest.mock import patch

from micsync.notify import (
    build_stop_command,
    build_completion_message,
    build_incomplete_message,
    build_start_message,
    build_stopped_message,
    copy_to_clipboard,
)


class NotifyTest(unittest.TestCase):
    def test_start_message_can_include_stop_hint(self) -> None:
        message = build_start_message(
            candidate_count=3,
            total_bytes=1024,
            existing_count=2,
            stop_hint="copied exact stop command to clipboard",
        )
        self.assertIn("3 candidate files", message)
        self.assertIn("2 already exist", message)
        self.assertIn("stop:", message)

    def test_completion_message_includes_counts_and_elapsed_time(self) -> None:
        message = build_completion_message(
            mirrored_count=3,
            derived_count=2,
            duplicate_count=1,
            rescan_existing=4,
            failed_count=0,
            warning_count=2,
            total_bytes=1024,
            elapsed_seconds=12,
            ejected_volumes=["MIC 01", "MIC 02"],
        )
        self.assertIn("3 imported", message)
        self.assertIn("2 organized", message)
        self.assertIn("1 duplicate", message)
        self.assertIn("4 rescan existing", message)
        self.assertIn("2 warning", message)
        self.assertIn("12s", message)

    def test_incomplete_message_includes_mirror_and_derived_counts(self) -> None:
        message = build_incomplete_message(
            mirrored_count=1,
            derived_count=0,
            duplicate_count=0,
            failed_count=1,
            warning_count=0,
            total_bytes=512,
            elapsed_seconds=3,
        )
        self.assertIn("1 imported", message)
        self.assertIn("0 organized", message)
        self.assertIn("1 failed", message)

    def test_stopped_message_includes_summary(self) -> None:
        message = build_stopped_message(
            mirrored_count=2,
            derived_count=1,
            duplicate_count=1,
            warning_count=1,
            total_bytes=2048,
            elapsed_seconds=7,
        )
        self.assertIn("stopped", message)
        self.assertIn("2 imported", message)
        self.assertIn("1 organized", message)
        self.assertIn("1 duplicate", message)
        self.assertIn("7s", message)

    def test_build_stop_command_uses_absolute_paths(self) -> None:
        command = build_stop_command(
            service_root=Path("/srv/custom-checkout"),
            data_root=Path("/var/lib/micSync-data"),
        )
        self.assertEqual(
            command,
            "NEXUS_DATA_ROOT=/var/lib/micSync-data "
            "/srv/custom-checkout/scripts/micSync.sh --stop",
        )

    def test_build_stop_command_does_not_assume_checkout_name(self) -> None:
        command = build_stop_command(
            service_root=Path("/srv/audio-importer"),
            data_root=Path("/var/lib/micSync-data"),
        )
        self.assertIn(
            "/srv/audio-importer/scripts/micSync.sh --stop",
            command,
        )

    @patch("micsync.notify.subprocess.run")
    def test_copy_to_clipboard_returns_true_on_success(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        self.assertTrue(copy_to_clipboard("hello"))
        mock_run.assert_called_once()
