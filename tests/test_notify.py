import unittest

from micsync.notify import build_completion_message


class NotifyTest(unittest.TestCase):
    def test_completion_message_includes_counts_and_elapsed_time(self) -> None:
        message = build_completion_message(
            imported_count=3,
            duplicate_count=1,
            failed_count=0,
            total_bytes=1024,
            elapsed_seconds=12,
            ejected_volumes=["MIC 01", "MIC 02"],
        )
        self.assertIn("3 imported", message)
        self.assertIn("1 duplicate", message)
        self.assertIn("12s", message)
