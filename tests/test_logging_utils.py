import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from micsync.logging_utils import (
    build_event_line,
    build_run_logger,
    build_progress_line,
)


class LoggingUtilsTest(unittest.TestCase):
    def test_build_event_line_formats_timestamp_and_kind(self) -> None:
        line = build_event_line(
            "micSync scanning mounted volumes",
            kind="event",
            when=datetime(2026, 3, 16, 2, 43, 58),
        )

        self.assertEqual(
            line,
            "26.03.16 02:43:58 | event     | micSync scanning mounted volumes",
        )

    def test_build_progress_line_formats_gigabytes_and_percentage(self) -> None:
        line = build_progress_line(
            action="mirror",
            current_index=3,
            total_count=12,
            processed_bytes=15_100_000_000,
            total_bytes=44_100_000_000,
            file_size_bytes=38_070_000,
            path="raw/MIC_01/TX_MIC002_20260309_191135/TX00_MIC021_20260310_212650_edit.wav",
            when=datetime(2026, 3, 16, 2, 43, 58),
        )

        self.assertEqual(
            line,
            "26.03.16 02:43:58 | mirror    |  3/12 | 15.1/44.1 GB, 34% |  38.07MB | raw/MIC_01/TX_MIC002_20260309_191135/TX00_MIC021_20260310_212650_edit.wav",
        )

    def test_build_run_logger_appends_to_file_and_stdout_when_echo_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "runs.log"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                log_event = build_run_logger(log_path=log_path, echo_to_stdout=True)
                log_event("micSync event")

            self.assertIn("micSync event", stdout.getvalue())
            self.assertIn("micSync event", log_path.read_text(encoding="utf-8"))

    def test_build_run_logger_skips_stdout_when_echo_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "runs.log"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                log_event = build_run_logger(log_path=log_path, echo_to_stdout=False)
                log_event("micSync event")

            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("micSync event", log_path.read_text(encoding="utf-8"))
