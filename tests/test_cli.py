import os
import subprocess
import sys
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = SERVICE_ROOT.parents[1]


class CliSmokeTest(unittest.TestCase):
    def test_help_exits_successfully(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "micsync.cli", "--help"],
            cwd=SERVICE_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())

    def test_wrapper_prefers_explicit_environment_roots(self) -> None:
        env = os.environ.copy()
        env["NEXUS_DEPLOY_ROOT"] = str(DEPLOY_ROOT)
        env["NEXUS_DATA_ROOT"] = "/tmp/micsync-test-data"
        result = subprocess.run(
            [str(DEPLOY_ROOT / "scripts" / "micsync-import.sh"), "--help"],
            cwd=DEPLOY_ROOT.parents[1],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())
