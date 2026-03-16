import unittest
from pathlib import Path

from micsync.config import build_config


class ConfigTest(unittest.TestCase):
    def test_default_paths_use_service_and_shared_audio_roots(self) -> None:
        cfg = build_config(
            nexus_data_root=Path("/tmp/nexus-data"),
            env={},
        )
        self.assertEqual(cfg.runtime_root, Path("/tmp/nexus-data/micSync"))
        self.assertEqual(cfg.recordings_root, Path("/tmp/nexus-data/recordings/audio"))
        self.assertEqual(cfg.recordings_raw_root, Path("/tmp/nexus-data/recordings/audio/raw"))
        self.assertEqual(
            cfg.recordings_derived_root,
            Path("/tmp/nexus-data/recordings/audio/derived"),
        )
        self.assertEqual(
            cfg.recordings_db_path,
            Path("/tmp/nexus-data/recordings/audio/db/recordings.sqlite3"),
        )
        self.assertEqual(cfg.recordings_tmp_root, Path("/tmp/nexus-data/recordings/audio/tmp"))
        self.assertFalse(cfg.enable_derived_outputs)
        self.assertEqual(cfg.derived_outputs_strategy, "clone_then_copy")
        self.assertIsNone(cfg.max_file_size_mb)
        self.assertEqual(cfg.segment_cadence_seconds, 1800)
        self.assertEqual(cfg.segment_group_tolerance_ms, 1000)

    def test_grouping_and_derivation_overrides_are_loaded(self) -> None:
        cfg = build_config(
            nexus_data_root=Path("/tmp/nexus-data"),
            env={
                "MICSYNC_SEGMENT_CADENCE_SECONDS": "900",
                "MICSYNC_SEGMENT_GROUP_TOLERANCE_MS": "250",
                "MICSYNC_ENABLE_DERIVED_OUTPUTS": "true",
                "MICSYNC_DERIVED_OUTPUTS_STRATEGY": "copy_only",
            },
        )
        self.assertEqual(cfg.segment_cadence_seconds, 900)
        self.assertEqual(cfg.segment_group_tolerance_ms, 250)
        self.assertTrue(cfg.enable_derived_outputs)
        self.assertEqual(cfg.derived_outputs_strategy, "copy_only")
