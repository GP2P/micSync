import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from micsync.logging_utils import build_run_logger


class LoggingUtilsTest(unittest.TestCase):
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
