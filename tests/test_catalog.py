import tempfile
import unittest
from pathlib import Path

from micsync.catalog import Catalog


class CatalogTest(unittest.TestCase):
    def test_upsert_take_creates_one_take_and_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            take_id_1 = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
            )
            take_id_2 = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
            )
            self.assertEqual(take_id_1, take_id_2)
            row = catalog.fetch_take(take_id_1)
            self.assertIsNotNone(row["first_imported_at"])

    def test_artifact_rows_preserve_segment_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            take_id = catalog.upsert_take(
                take_key="20260608_112048_TX02_MIC001",
                take_start_at="2026-06-08T11:20:48",
                take_end_at=None,
                tx_slot="TX02",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
            )
            segment_id = catalog.upsert_segment(
                take_id=take_id,
                segment_key="20260608_112048_TX02_MIC001",
                segment_start_at="2026-06-08T11:20:48",
                segment_end_at=None,
                tx_slot="TX02",
                mic_sequence="MIC001",
                physical_mic_id=2,
                source_parent_folder="TX_MIC001_20260308_143058",
                duration_ms=None,
                first_seen_at="2026-06-08T11:21:00",
                last_attempted_at="2026-06-08T11:21:00",
                completed_at="2026-06-08T11:21:00",
            )
            artifact_id = catalog.insert_artifact(
                take_id=take_id,
                segment_id=segment_id,
                segment_key="20260608_112048_TX02_MIC001",
                source_filename="TX02_MIC001_20260608_112048_orig.wav",
                import_status="imported",
            )
            row = catalog.fetch_artifact(artifact_id)
            self.assertEqual(row["segment_key"], "20260608_112048_TX02_MIC001")
