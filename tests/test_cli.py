import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from micsync.cli import run_import
from micsync.config import Config
from micsync.importer import MirrorOutcome
from micsync.lock import LockAcquireResult
from micsync.scanner import CandidateFile


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
        env["NEXUS_DEPLOY_ROOT"] = "/tmp/micSync-deploy-root"
        env["NEXUS_DATA_ROOT"] = "/tmp/micSync-test-data"
        result = subprocess.run(
            [str(SERVICE_ROOT / "scripts" / "micSync.sh"), "--help"],
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
            [str(SERVICE_ROOT / "scripts" / "micSync.sh"), "--help"],
            cwd=SERVICE_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())

    def test_standalone_wrapper_stop_command_exits_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = os.environ.copy()
            env.pop("NEXUS_DEPLOY_ROOT", None)
            env.pop("NEXUS_DATA_ROOT", None)
            env["HOME"] = tmpdir
            result = subprocess.run(
                [str(SERVICE_ROOT / "scripts" / "micSync.sh"), "--stop"],
                cwd=SERVICE_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("micSync", result.stdout)

    def test_standalone_wrapper_uses_home_env_file_for_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            home_dir = tmp_root / "home"
            data_root = tmp_root / "nexus-data"
            config_dir = home_dir / ".config" / "nexus"
            run_dir = data_root / "micSync" / "run"
            config_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            (config_dir / "env.sh").write_text(
                f'export NEXUS_DATA_ROOT="{data_root}"\n',
                encoding="utf-8",
            )
            (run_dir / "active.lock").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "hostname": "test-host",
                        "started_at": "2026-03-15T00:00:00+00:00",
                        "last_heartbeat_at": "2999-01-01T00:00:00+00:00",
                        "phase": "test",
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.pop("NEXUS_DEPLOY_ROOT", None)
            env.pop("NEXUS_DATA_ROOT", None)
            env["HOME"] = str(home_dir)
            result = subprocess.run(
                [
                    str(SERVICE_ROOT / "scripts" / "micSync.sh"),
                    "--stop",
                    "--notify",
                    "false",
                ],
                cwd=SERVICE_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("micSync stop requested", result.stdout)


class CliRunTest(unittest.TestCase):
    def test_failed_derive_counts_mirror_and_derive_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            config = Config(
                runtime_root=tmp_path / "runtime",
                recordings_root=recordings_root,
                recordings_raw_root=recordings_root / "raw",
                recordings_derived_root=recordings_root / "derived",
                recordings_db_path=recordings_root / "db" / "recordings.sqlite3",
                recordings_tmp_root=recordings_root / "tmp",
                max_file_size_mb=None,
                extension_allowlist=(".wav",),
                variant_policy="all",
                enable_derived_outputs=False,
                derived_outputs_strategy="clone_then_copy",
                segment_cadence_seconds=1800,
                segment_group_tolerance_ms=1000,
                stale_lock_timeout_seconds=300,
                notify=True,
                eject=False,
            )
            candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_120000.wav",
                source_parent_folder="A",
                file_size_bytes=128,
            )
            mirrored = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_01" / "A" / candidate.source_path.name,
                checksum="abc123",
                size_bytes=128,
                status="mirrored",
                source_file_id=1,
                warning_count=0,
            )

            class FakeLock:
                def acquire_or_request_rescan(self) -> LockAcquireResult:
                    return LockAcquireResult(
                        acquired=True,
                        recovered_stale_lock=False,
                        requested_rescan=False,
                    )

                def request_stop(self) -> bool:
                    return True

                def refresh(self, phase: str) -> None:
                    return None

                def consume_stop_request(self) -> bool:
                    return False

                def consume_rescan_request(self) -> bool:
                    return False

                def release(self) -> None:
                    return None

            args = argparse.Namespace(
                max_file_size_mb=None,
                notify=None,
                eject=None,
                stop=False,
            )
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[candidate]),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=False),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch(
                    "micsync.cli.derive_mirrored_recording",
                    side_effect=RuntimeError("derive failed"),
                ),
                mock.patch("micsync.cli.build_incomplete_message", return_value="incomplete")
                as build_incomplete_message,
            ):
                result = run_import(args)
                log_contents = (config.runtime_root / "logs" / "runs.log").read_text(
                    encoding="utf-8"
                )

        self.assertEqual(result, 1)
        self.assertEqual(build_incomplete_message.call_count, 1)
        self.assertEqual(build_incomplete_message.call_args.kwargs["mirrored_count"], 1)
        self.assertEqual(build_incomplete_message.call_args.kwargs["derived_count"], 0)
        self.assertEqual(build_incomplete_message.call_args.kwargs["duplicate_count"], 0)
        self.assertEqual(build_incomplete_message.call_args.kwargs["failed_count"], 1)
        self.assertIn("summary mirrored=1 derived=0 duplicate=0 failed=1", log_contents)
