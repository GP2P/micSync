import tempfile
import unittest
from pathlib import Path

from micsync.lock import LockManager


class LockTest(unittest.TestCase):
    def test_stale_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LockManager(Path(tmpdir), stale_timeout_seconds=30)
            manager.write_stale_lock_for_test(pid=999999, heartbeat_age_seconds=999)
            result = manager.acquire_or_request_rescan()
            self.assertTrue(result.acquired)
            self.assertTrue(result.recovered_stale_lock)

    def test_stop_request_requires_active_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LockManager(Path(tmpdir), stale_timeout_seconds=30)
            self.assertFalse(manager.request_stop())
            result = manager.acquire_or_request_rescan()
            self.assertTrue(result.acquired)
            self.assertTrue(manager.request_stop())
            self.assertTrue(manager.consume_stop_request())
            self.assertFalse(manager.consume_stop_request())
