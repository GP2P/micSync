import os
import subprocess
import sys
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]


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

    def test_standalone_wrapper_prefers_explicit_environment_roots(self) -> None:
        env = os.environ.copy()
        env["NEXUS_DEPLOY_ROOT"] = "/tmp/micsync-deploy-root"
        env["NEXUS_DATA_ROOT"] = "/tmp/micsync-test-data"
        result = subprocess.run(
            [str(SERVICE_ROOT / "scripts" / "micsync.sh"), "--help"],
            cwd=SERVICE_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())

    def test_standalone_wrapper_works_without_preexisting_environment(self) -> None:
        env = os.environ.copy()
        env.pop("NEXUS_DEPLOY_ROOT", None)
        env.pop("NEXUS_DATA_ROOT", None)
        result = subprocess.run(
            [str(SERVICE_ROOT / "scripts" / "micsync.sh"), "--help"],
            cwd=SERVICE_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())
