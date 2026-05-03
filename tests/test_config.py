import unittest
from pathlib import Path

from micsync.config import build_config


class ConfigTest(unittest.TestCase):
    def test_default_paths_use_downloads_home(self) -> None:
        cfg = build_config(
            micsync_home=Path("/home/alice/Downloads/micSync"),
            env={},
        )
        self.assertEqual(cfg.runtime_root, Path("/home/alice/Downloads/micSync/runtime"))
        self.assertEqual(cfg.recordings_root, Path("/home/alice/Downloads/micSync/recordings"))
        self.assertEqual(cfg.recordings_raw_root, Path("/home/alice/Downloads/micSync/recordings/raw"))
        self.assertEqual(
            cfg.recordings_derived_root,
            Path("/home/alice/Downloads/micSync/recordings/organized"),
        )
        self.assertEqual(
            cfg.recordings_db_path,
            Path("/home/alice/Downloads/micSync/recordings/db/recordings.sqlite3"),
        )
        self.assertEqual(cfg.recordings_tmp_root, Path("/home/alice/Downloads/micSync/recordings/tmp"))
        self.assertTrue(cfg.enable_derived_outputs)
        self.assertEqual(cfg.derived_outputs_strategy, "auto")
        self.assertEqual(cfg.organized_layout, "timeline")
        self.assertIsNone(cfg.max_file_size_mb)
        self.assertEqual(cfg.segment_cadence_seconds, 1800)
        self.assertEqual(cfg.segment_group_tolerance_ms, 1000)

    def test_grouping_and_derivation_overrides_are_loaded(self) -> None:
        cfg = build_config(
            micsync_home=Path("/tmp/micSync"),
            env={
                "MICSYNC_SEGMENT_CADENCE_SECONDS": "900",
                "MICSYNC_SEGMENT_GROUP_TOLERANCE_MS": "250",
                "MICSYNC_ENABLE_DERIVED_OUTPUTS": "true",
                "MICSYNC_DERIVED_OUTPUTS_STRATEGY": "copy_only",
                "MICSYNC_ORGANIZED_LAYOUT": "dji",
            },
        )
        self.assertEqual(cfg.segment_cadence_seconds, 900)
        self.assertEqual(cfg.segment_group_tolerance_ms, 250)
        self.assertTrue(cfg.enable_derived_outputs)
        self.assertEqual(cfg.derived_outputs_strategy, "copy_only")
        self.assertEqual(cfg.organized_layout, "dji")

    def test_path_overrides_expand_micsync_home(self) -> None:
        cfg = build_config(
            micsync_home=Path("/tmp/micSync"),
            env={
                "MICSYNC_RUNTIME_ROOT": "$MICSYNC_HOME/state",
                "MICSYNC_RECORDINGS_ROOT": "$MICSYNC_HOME/audio",
                "MICSYNC_RECORDINGS_DB_PATH": "$MICSYNC_HOME/audio/catalog.sqlite3",
            },
        )

        self.assertEqual(cfg.runtime_root, Path("/tmp/micSync/state"))
        self.assertEqual(cfg.recordings_root, Path("/tmp/micSync/audio"))
        self.assertEqual(cfg.recordings_db_path, Path("/tmp/micSync/audio/catalog.sqlite3"))
