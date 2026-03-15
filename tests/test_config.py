import unittest
from pathlib import Path

from micsync.config import build_config


class ConfigTest(unittest.TestCase):
    def test_default_paths_use_service_and_shared_roots(self) -> None:
        cfg = build_config(
            nexus_data_root=Path("/tmp/nexus-data"),
            env={},
        )
        self.assertEqual(cfg.runtime_root, Path("/tmp/nexus-data/micSync"))
        self.assertEqual(cfg.recordings_root, Path("/tmp/nexus-data/recordings"))
        self.assertEqual(
            cfg.recordings_db_path,
            Path("/tmp/nexus-data/recordings/db/recordings.sqlite3"),
        )
        self.assertIsNone(cfg.max_file_size_mb)
