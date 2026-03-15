import tempfile
import unittest
from pathlib import Path

from micsync.catalog import Catalog


class CatalogTest(unittest.TestCase):
    def test_upsert_creates_one_recording_and_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            recording_id_1 = catalog.upsert_recording(
                recording_group_key="20260608_112048_TX02_MIC001",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                tx_slot="TX02",
                mic_sequence="MIC001",
                physical_mic_id=2,
            )
            recording_id_2 = catalog.upsert_recording(
                recording_group_key="20260608_112048_TX02_MIC001",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                tx_slot="TX02",
                mic_sequence="MIC001",
                physical_mic_id=2,
            )
            self.assertEqual(recording_id_1, recording_id_2)

    def test_recording_file_rows_preserve_group_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "recordings.sqlite3"
            catalog = Catalog(db_path)
            recording_id = catalog.upsert_recording(
                recording_group_key="20260608_112048_TX02_MIC001",
                recording_start_at="2026-06-08T11:20:48",
                recording_end_at=None,
                tx_slot="TX02",
                mic_sequence="MIC001",
                physical_mic_id=2,
            )
            file_id = catalog.insert_recording_file(
                recording_id=recording_id,
                recording_group_key="20260608_112048_TX02_MIC001",
                source_filename="TX02_MIC001_20260608_112048_orig.wav",
                import_status="imported",
            )
            row = catalog.fetch_recording_file(file_id)
            self.assertEqual(row["recording_group_key"], "20260608_112048_TX02_MIC001")
