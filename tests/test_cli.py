import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

from micsync.catalog import Catalog
from micsync.cli import _recordings_root_supports_clone, build_parser, run_import
from micsync.config import Config
from micsync.eject import EjectResult
from micsync.importer import MirrorOutcome
from micsync.lock import LockAcquireResult
from micsync.scanner import CandidateFile


SERVICE_ROOT = Path(__file__).resolve().parents[1]


class CliSmokeTest(unittest.TestCase):
    def test_clone_support_probe_uses_mount_point_for_subdirectory(self) -> None:
        df_output = (
            "Filesystem 512-blocks Used Available Capacity iused ifree %iused Mounted on\n"
            "/dev/disk3s5 1942638920 1729896024 133400344 93% 5663466 667001720 1% /System/Volumes/Data\n"
        )
        diskutil_output = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
    <key>FilesystemType</key>
    <string>apfs</string>
</dict>
</plist>
"""

        with mock.patch("micsync.cli.subprocess.run") as run_mock:
            run_mock.side_effect = [
                mock.Mock(returncode=0, stdout=df_output, stderr=""),
                mock.Mock(returncode=0, stdout=diskutil_output, stderr=b""),
            ]

            supported, reason = _recordings_root_supports_clone(
                Path("~/nexus-data/recordings/audio")
            )

        self.assertTrue(supported)
        self.assertIsNone(reason)
        self.assertEqual(run_mock.call_args_list[0].args[0], ["df", "-P", "~/nexus-data/recordings/audio"])
        self.assertEqual(
            run_mock.call_args_list[1].args[0],
            ["diskutil", "info", "-plist", "/System/Volumes/Data"],
        )

    def test_build_parser_accepts_repeatable_source_volumes_and_derived_toggle(self) -> None:
        args = build_parser().parse_args(
            [
                "--source-volume",
                "/Volumes/MIC 01",
                "--source-volume",
                "/Volumes/MIC 02",
                "--derived",
                "false",
            ]
        )

        self.assertEqual(args.source_volume, ["/Volumes/MIC 01", "/Volumes/MIC 02"])
        self.assertEqual(args.derived, "false")

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

    def test_standalone_wrapper_prefers_explicit_data_root(self) -> None:
        env = os.environ.copy()
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

    def test_standalone_wrapper_detaches_normal_import_and_preserves_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            fake_bin = tmp_root / "bin"
            fake_bin.mkdir()
            capture_path = tmp_root / "capture.json"
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                "\n".join(
                    [
                        "#!/bin/zsh",
                        f"{sys.executable} - <<'PY' \"$@\"",
                        "import json",
                        "import os",
                        "import pathlib",
                        "import sys",
                        "",
                        f"capture_path = pathlib.Path({str(capture_path)!r})",
                        "capture_path.write_text(",
                        "    json.dumps(",
                        "        {",
                        '            "argv": sys.argv[1:],',
                        '            "NEXUS_DATA_ROOT": os.environ.get("NEXUS_DATA_ROOT"),',
                        "        }",
                        "    ),",
                        '    encoding="utf-8",',
                        ")",
                        "PY",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["NEXUS_DATA_ROOT"] = "/tmp/micSync-data-root"
            result = subprocess.run(
                [
                    str(SERVICE_ROOT / "scripts" / "micSync.sh"),
                    "--source-volume",
                    "/Volumes/MIC 01",
                    "--source-volume",
                    "/Volumes/MIC 02",
                    "--derived",
                    "false",
                    "--notify",
                    "false",
                ],
                cwd=SERVICE_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0)
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(
                capture["argv"],
                [
                    "-m",
                    "micsync.cli",
                    "--detach",
                    "--source-volume",
                    "/Volumes/MIC 01",
                    "--source-volume",
                    "/Volumes/MIC 02",
                    "--derived",
                    "false",
                    "--notify",
                    "false",
                ],
            )
            self.assertEqual(capture["NEXUS_DATA_ROOT"], "/tmp/micSync-data-root")

    def test_wrapper_exports_pythonpath_for_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            fake_bin = tmp_root / "bin"
            fake_bin.mkdir()
            capture_path = tmp_root / "capture.json"
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                "\n".join(
                    [
                        "#!/bin/zsh",
                        f"{sys.executable} - <<'PY' \"$@\"",
                        "import json",
                        "import os",
                        "import pathlib",
                        "import sys",
                        "",
                        f"capture_path = pathlib.Path({str(capture_path)!r})",
                        "capture_path.write_text(",
                        "    json.dumps(",
                        "        {",
                        '            "argv": sys.argv[1:],',
                        '            "PYTHONPATH": os.environ.get("PYTHONPATH"),',
                        "        }",
                        "    ),",
                        '    encoding="utf-8",',
                        ")",
                        "PY",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env.pop("NEXUS_DATA_ROOT", None)
            env["HOME"] = str(tmp_root / "home")
            result = subprocess.run(
                [
                    str(SERVICE_ROOT / "scripts" / "micSync.sh"),
                    "--notify",
                    "false",
                ],
                cwd=SERVICE_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0)
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(capture["PYTHONPATH"], str(SERVICE_ROOT / "src"))

    def test_standalone_checkout_defaults_data_root_to_local_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            standalone_root = tmp_root / "audio-importer"
            (standalone_root / "scripts").mkdir(parents=True)
            (standalone_root / "src").mkdir()
            shutil.copy2(
                SERVICE_ROOT / "scripts" / "micSync.sh",
                standalone_root / "scripts" / "micSync.sh",
            )
            (standalone_root / "scripts" / "micSync.sh").chmod(0o755)

            fake_bin = tmp_root / "bin"
            fake_bin.mkdir()
            capture_path = tmp_root / "capture.json"
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                "\n".join(
                    [
                        "#!/bin/zsh",
                        f"{sys.executable} - <<'PY' \"$@\"",
                        "import json",
                        "import os",
                        "import pathlib",
                        "import sys",
                        "",
                        f"capture_path = pathlib.Path({str(capture_path)!r})",
                        "capture_path.write_text(",
                        "    json.dumps(",
                        "        {",
                        '            "argv": sys.argv[1:],',
                        '            "NEXUS_DATA_ROOT": os.environ.get("NEXUS_DATA_ROOT"),',
                        "        }",
                        "    ),",
                        '    encoding="utf-8",',
                        ")",
                        "PY",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env.pop("NEXUS_DATA_ROOT", None)
            env["HOME"] = str(tmp_root / "home")
            result = subprocess.run(
                [
                    str(standalone_root / "scripts" / "micSync.sh"),
                    "--notify",
                    "false",
                ],
                cwd=standalone_root,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0)
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(capture["NEXUS_DATA_ROOT"], str(standalone_root / "data"))


class CliRunTest(unittest.TestCase):
    def test_preflight_disables_derived_outputs_when_clone_support_is_missing(self) -> None:
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
                enable_derived_outputs=True,
                derived_outputs_strategy="clone_then_copy",
                segment_cadence_seconds=1800,
                segment_group_tolerance_ms=1000,
                stale_lock_timeout_seconds=300,
                notify=False,
                eject=False,
            )
            candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_120000.wav",
                source_parent_folder="A",
                file_size_bytes=128,
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
                derived=None,
                source_volume=None,
                stop=False,
                run_detached_child=False,
            )
            mirrored = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_01" / "A" / candidate.source_path.name,
                checksum="abc123",
                size_bytes=128,
                status="mirrored",
                source_file_id=1,
                warning_count=0,
            )

            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [candidate],
                        [],
                    ],
                ),
                mock.patch("micsync.cli._preflight_derived_outputs", return_value=(False, "recordings root is not APFS")),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=True),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch(
                    "micsync.cli._pending_derivation_queue",
                    side_effect=[
                        [(mirrored.source_file_id, mirrored.raw_path, candidate.source_path.name, 0)],
                        [],
                    ],
                ),
                mock.patch("micsync.cli.derive_mirrored_recording") as derive_mirrored_recording,
            ):
                derive_mirrored_recording.return_value = mock.Mock(warning_count=0)
                result = run_import(args)

            self.assertEqual(result, 0)
            self.assertFalse(derive_mirrored_recording.call_args.kwargs["enable_derived_outputs"])

    def test_foreground_run_prints_progress_to_stdout(self) -> None:
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
                notify=False,
                eject=False,
            )
            first_candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_120000.wav",
                source_parent_folder="A",
                file_size_bytes=233_000_000,
            )
            second_candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_123000.wav",
                source_parent_folder="A",
                file_size_bytes=167_000_000,
            )
            first_mirrored = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_01" / "A" / first_candidate.source_path.name,
                checksum="abc123",
                size_bytes=233_000_000,
                status="mirrored",
                source_file_id=1,
                warning_count=0,
            )
            second_mirrored = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_01" / "A" / second_candidate.source_path.name,
                checksum="def456",
                size_bytes=167_000_000,
                status="mirrored",
                source_file_id=2,
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
                derived=None,
                notify=None,
                eject=None,
                source_volume=None,
                stop=False,
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [first_candidate, second_candidate],
                        [],
                    ],
                ),
                mock.patch(
                    "micsync.cli.Catalog.fetch_pending_source_files_for_derivation",
                    side_effect=[
                        [
                            {
                                "id": 1,
                                "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
                                "source_filename": first_candidate.source_path.name,
                                "source_size_bytes": 233_000_000,
                            },
                            {
                                "id": 2,
                                "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_123000.wav",
                                "source_filename": second_candidate.source_path.name,
                                "source_size_bytes": 167_000_000,
                            },
                        ],
                        [],
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=True),
                mock.patch("micsync.cli.send_notification"),
                mock.patch(
                    "micsync.cli.mirror_recording_to_raw",
                    side_effect=[first_mirrored, second_mirrored],
                ),
                mock.patch("micsync.cli.derive_mirrored_recording", return_value=mock.Mock(warning_count=0)),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

        self.assertEqual(result, 0)
        self.assertIn("micSync mirror starting candidates=2 existing=0 total=400MB", stdout.getvalue())
        self.assertIn(
            "| mirror    |  1/2 |  0.2/ 0.4 GB,  58% | 233.00MB | raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
            stdout.getvalue(),
        )
        self.assertIn(
            "| mirror    |  2/2 |  0.4/ 0.4 GB, 100% | 167.00MB | raw/MIC_01/A/TX01_MIC001_20260315_123000.wav",
            stdout.getvalue(),
        )

    def test_normalize_progress_uses_derived_relative_path(self) -> None:
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
                enable_derived_outputs=True,
                derived_outputs_strategy="copy_only",
                segment_cadence_seconds=1800,
                segment_group_tolerance_ms=1000,
                stale_lock_timeout_seconds=300,
                notify=False,
                eject=False,
            )
            candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_120000.wav",
                source_parent_folder="A",
                file_size_bytes=233_000_000,
            )
            mirrored = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_01" / "A" / candidate.source_path.name,
                checksum="abc123",
                size_bytes=233_000_000,
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
                derived=None,
                notify=None,
                eject=None,
                source_volume=None,
                stop=False,
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[candidate]),
                mock.patch(
                    "micsync.cli._preflight_derived_outputs",
                    return_value=(True, None),
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=True),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch(
                    "micsync.cli._pending_derivation_queue",
                    return_value=[(1, mirrored.raw_path, candidate.source_path.name, 0, 233_000_000)],
                ),
                mock.patch(
                    "micsync.cli.derive_mirrored_recording",
                    return_value=mock.Mock(
                        warning_count=0,
                        size_bytes=233_000_000,
                        derived_path=recordings_root
                        / "derived"
                        / "normalized"
                        / "2026"
                        / "03"
                        / "15"
                        / "20260315_120000_TX01_MIC001.wav",
                    ),
                ),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

        self.assertEqual(result, 0)
        self.assertIn(
            "| normalize |  1/1 |  0.2/ 0.2 GB, 100% | 233.00MB | derived/normalized/2026/03/15/20260315_120000_TX01_MIC001.wav",
            stdout.getvalue(),
        )

    def test_run_import_prefilters_existing_duplicates_before_notification_and_mirror_progress(self) -> None:
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
            duplicate_candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_120000.wav",
                source_parent_folder="A",
                file_size_bytes=233_000_000,
            )
            new_candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_123000.wav",
                source_parent_folder="A",
                file_size_bytes=167_000_000,
            )
            new_outcome = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_01" / "A" / new_candidate.source_path.name,
                checksum="new",
                size_bytes=167_000_000,
                status="mirrored",
                source_file_id=2,
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
                derived=None,
                notify=None,
                eject=None,
                source_volume=None,
                stop=False,
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [duplicate_candidate, new_candidate],
                        [duplicate_candidate, new_candidate],
                    ],
                ),
                mock.patch(
                    "micsync.cli.find_preexisting_raw_duplicate",
                    side_effect=[
                        recordings_root / "raw" / "MIC_01" / "A" / duplicate_candidate.source_path.name,
                        None,
                        recordings_root / "raw" / "MIC_01" / "A" / duplicate_candidate.source_path.name,
                        recordings_root / "raw" / "MIC_01" / "A" / new_candidate.source_path.name,
                    ],
                ),
                mock.patch("micsync.cli._pending_derivation_queue", return_value=[]),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=False),
                mock.patch("micsync.cli.send_notification") as send_notification,
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=new_outcome) as mirror_recording_to_raw,
                redirect_stdout(stdout),
            ):
                result = run_import(args)

        self.assertEqual(result, 0)
        self.assertIn("micSync mirror starting candidates=1 existing=1 total=167MB", stdout.getvalue())
        self.assertIn(
            "| mirror    |  1/1 |  0.2/ 0.2 GB, 100% | 167.00MB | raw/MIC_01/A/TX01_MIC001_20260315_123000.wav",
            stdout.getvalue(),
        )
        self.assertNotIn("TX01_MIC001_20260315_120000.wav", stdout.getvalue())
        mirror_recording_to_raw.assert_called_once()
        start_notification = next(
            kwargs
            for _, kwargs in send_notification.call_args_list
            if kwargs.get("title") == "micSync mirror starting"
        )
        self.assertIn("1 already exist", start_notification["message"])

    def test_attached_run_echoes_importer_log_events_to_stdout_and_log(self) -> None:
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
                notify=False,
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

            def mirror_side_effect(**kwargs):
                kwargs["log_event"]("mirrored test-file")
                return mirrored

            def derive_side_effect(**kwargs):
                kwargs["log_event"]("derived test-file")
                return mock.Mock(warning_count=0)

            args = argparse.Namespace(
                max_file_size_mb=None,
                notify=None,
                eject=None,
                stop=False,
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[candidate]),
                mock.patch(
                    "micsync.cli.Catalog.fetch_pending_source_files_for_derivation",
                    return_value=[
                        {
                            "id": 1,
                            "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
                            "source_filename": candidate.source_path.name,
                        }
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=False),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", side_effect=mirror_side_effect),
                mock.patch("micsync.cli.derive_mirrored_recording", side_effect=derive_side_effect),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

            log_contents = (config.runtime_root / "logs" / "runs.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result, 0)
        self.assertIn("mirrored test-file", stdout.getvalue())
        self.assertIn("derived test-file", stdout.getvalue())
        self.assertIn("mirrored test-file", log_contents)
        self.assertIn("derived test-file", log_contents)

    def test_busy_lock_logs_rescan_request(self) -> None:
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
                notify=False,
                eject=False,
            )

            class BusyLock:
                def acquire_or_request_rescan(self) -> LockAcquireResult:
                    return LockAcquireResult(
                        acquired=False,
                        recovered_stale_lock=False,
                        requested_rescan=True,
                    )

            args = argparse.Namespace(
                max_file_size_mb=None,
                notify=None,
                eject=None,
                stop=False,
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=BusyLock()),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

            log_contents = (config.runtime_root / "logs" / "runs.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result, 0)
        self.assertIn("lock busy", stdout.getvalue())
        self.assertIn("requested_rescan=true", stdout.getvalue())
        self.assertIn("lock busy", log_contents)
        self.assertIn("requested_rescan=true", log_contents)

    def test_noop_run_logs_no_candidates_detected(self) -> None:
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
                notify=False,
                eject=False,
            )

            class IdleLock:
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
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=IdleLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[]),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

            log_contents = (config.runtime_root / "logs" / "runs.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result, 0)
        self.assertIn("no candidates detected", stdout.getvalue())
        self.assertIn("no candidates detected", log_contents)

    def test_run_rotates_oversized_log_after_completion(self) -> None:
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
                notify=False,
                eject=False,
            )

            class IdleLock:
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

            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=IdleLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[]),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.HOT_RUN_LOG_MAX_BYTES", 1),
            ):
                result = run_import(
                    argparse.Namespace(
                        max_file_size_mb=None,
                        notify=None,
                        eject=None,
                        stop=False,
                        run_detached_child=False,
                    )
                )

            rotated_logs = sorted((config.runtime_root / "logs").glob("runs-*.log"))
            self.assertEqual(result, 0)
            self.assertEqual(len(rotated_logs), 1)
            self.assertFalse((config.runtime_root / "logs" / "runs.log").exists())
            self.assertIn("micSync rotating oversized log", rotated_logs[0].read_text(encoding="utf-8"))

    def test_scan_logs_volume_start_and_finish_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            volume_one = tmp_path / "Volumes" / "MIC 01"
            volume_two = tmp_path / "Volumes" / "MIC 02"
            volume_one.mkdir(parents=True)
            volume_two.mkdir(parents=True)
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
                notify=False,
                eject=False,
            )

            class IdleLock:
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

            def scan_side_effect(**kwargs):
                kwargs["on_volume_start"](volume_one)
                kwargs["on_volume_complete"](volume_one, 0)
                kwargs["on_volume_start"](volume_two)
                kwargs["on_volume_complete"](volume_two, 0)
                return []

            args = argparse.Namespace(
                max_file_size_mb=None,
                notify=None,
                eject=None,
                source_volume=[str(volume_one), str(volume_two)],
                stop=False,
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=IdleLock()),
                mock.patch("micsync.cli.scan_candidates", side_effect=scan_side_effect),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

        self.assertEqual(result, 0)
        output = stdout.getvalue()
        self.assertIn("micSync scan started volumes=2", output)
        self.assertIn(
            f"requested_volumes=['{volume_one}', '{volume_two}']",
            output,
        )
        self.assertIn("micSync scan volume started label=MIC 01", output)
        self.assertIn("micSync scan volume complete label=MIC 01 candidates=0", output)
        self.assertIn("micSync scan volume started label=MIC 02", output)
        self.assertIn("micSync scan volume complete label=MIC 02 candidates=0", output)
        self.assertIn("micSync scan complete candidates=0 volumes=2", output)
        self.assertIn("micSync duplicate preflight started candidates=0", output)
        self.assertIn("micSync duplicate preflight complete new=0 existing=0 duplicate_only_volumes=0", output)

    def test_scan_counts_only_actually_scanned_mounted_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            volume_one = tmp_path / "Volumes" / "MIC 01"
            missing_volume = tmp_path / "Volumes" / "MIC 02"
            volume_one.mkdir(parents=True)
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
                notify=False,
                eject=False,
            )

            class IdleLock:
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

            def scan_side_effect(**kwargs):
                kwargs["on_volume_start"](volume_one)
                kwargs["on_volume_complete"](volume_one, 0)
                return []

            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=IdleLock()),
                mock.patch("micsync.cli.scan_candidates", side_effect=scan_side_effect),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                redirect_stdout(stdout),
            ):
                result = run_import(
                    argparse.Namespace(
                        max_file_size_mb=None,
                        notify=None,
                        eject=None,
                        source_volume=[str(volume_one), str(missing_volume)],
                        stop=False,
                        run_detached_child=False,
                    )
                )

        self.assertEqual(result, 0)
        output = stdout.getvalue()
        self.assertIn("micSync scan started volumes=1", output)
        self.assertIn(
            f"requested_volumes=['{volume_one}', '{missing_volume}']",
            output,
        )
        self.assertIn("micSync scan complete candidates=0 volumes=1", output)

    def test_eject_outcomes_are_logged_after_stable_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            volume = tmp_path / "Volumes" / "MIC 1"
            volume.mkdir(parents=True)
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
                notify=False,
                eject=True,
            )
            candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=volume,
                source_path=volume / "A" / "TX01_MIC001_20260315_120000.wav",
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

            class OneRunLock:
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
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=OneRunLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [candidate],
                        [],
                    ],
                ),
                mock.patch(
                    "micsync.cli.Catalog.fetch_pending_source_files_for_derivation",
                    side_effect=[
                        [
                            {
                                "id": 1,
                                "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
                                "source_filename": candidate.source_path.name,
                            }
                        ],
                        [],
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=False),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch("micsync.cli.derive_mirrored_recording", return_value=mock.Mock(warning_count=0)),
                mock.patch("micsync.cli.eject_volume", return_value=EjectResult(ok=True, detail=None)),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

            log_contents = (config.runtime_root / "logs" / "runs.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result, 0)
        self.assertIn("ejected volume MIC 1", stdout.getvalue())
        self.assertIn("micSync rescan stable; attempting eject", stdout.getvalue())
        self.assertIn("ejected volume MIC 1", log_contents)
        self.assertIn("summary mirrored=1 derived=1 duplicate=0 rescan_existing=0 failed=0 warning=0", log_contents)

    def test_logs_preflight_notification_derive_and_eject_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            volume = tmp_path / "Volumes" / "MIC 01"
            volume.mkdir(parents=True)
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
                enable_derived_outputs=True,
                derived_outputs_strategy="copy_only",
                segment_cadence_seconds=1800,
                segment_group_tolerance_ms=1000,
                stale_lock_timeout_seconds=300,
                notify=True,
                eject=True,
            )
            candidate = CandidateFile(
                volume_label="MIC 01",
                volume_root=volume,
                source_path=volume / "A" / "TX01_MIC001_20260315_120000.wav",
                source_parent_folder="A",
                file_size_bytes=233_000_000,
            )
            mirrored = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_01" / "A" / candidate.source_path.name,
                checksum="abc123",
                size_bytes=233_000_000,
                status="mirrored",
                source_file_id=1,
                warning_count=0,
            )

            class OneRunLock:
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

            scan_call_count = {"value": 0}

            def scan_side_effect(**kwargs):
                scan_call_count["value"] += 1
                if scan_call_count["value"] == 1:
                    kwargs["on_volume_start"](volume)
                    kwargs["on_volume_complete"](volume, 1)
                    return [candidate]
                return []

            args = argparse.Namespace(
                max_file_size_mb=None,
                derived=None,
                notify=None,
                eject=None,
                source_volume=[str(volume)],
                stop=False,
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=OneRunLock()),
                mock.patch("micsync.cli.scan_candidates", side_effect=scan_side_effect),
                mock.patch("micsync.cli.find_preexisting_raw_duplicate", return_value=None),
                mock.patch(
                    "micsync.cli._pending_derivation_queue",
                    side_effect=[
                        [(1, mirrored.raw_path, candidate.source_path.name, 0, 233_000_000)],
                        [],
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=True),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch(
                    "micsync.cli.derive_mirrored_recording",
                    return_value=mock.Mock(
                        warning_count=0,
                        size_bytes=233_000_000,
                        derived_path=recordings_root
                        / "derived"
                        / "normalized"
                        / "2026"
                        / "03"
                        / "15"
                        / "20260315_120000_TX01_MIC001.wav",
                    ),
                ),
                mock.patch("micsync.cli.eject_volume", return_value=EjectResult(ok=True, detail=None)),
                redirect_stdout(stdout),
            ):
                result = run_import(args)

        self.assertEqual(result, 0)
        output = stdout.getvalue()
        self.assertIn("micSync duplicate preflight started candidates=1", output)
        self.assertIn("micSync duplicate preflight complete new=1 existing=0 duplicate_only_volumes=0", output)
        self.assertIn("micSync copied stop command to clipboard", output)
        self.assertIn("micSync sent notification title=micSync mirror starting", output)
        self.assertIn("micSync derive starting candidates=1 total=233MB", output)
        self.assertIn("micSync derive complete processed=1", output)
        self.assertIn("micSync rescan stable; attempting eject", output)

    def test_rescan_existing_does_not_inflate_duplicate_count(self) -> None:
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
                notify=False,
                eject=True,
            )
            duplicate_only_candidate = CandidateFile(
                volume_label="MIC 1",
                volume_root=tmp_path / "Volumes" / "MIC 1",
                source_path=tmp_path / "Volumes" / "MIC 1" / "A" / "TX01_MIC001_20260315_120000.wav",
                source_parent_folder="A",
                file_size_bytes=128,
            )
            mixed_duplicate_candidate = CandidateFile(
                volume_label="MIC 2",
                volume_root=tmp_path / "Volumes" / "MIC 2",
                source_path=tmp_path / "Volumes" / "MIC 2" / "A" / "TX01_MIC001_20260315_123000.wav",
                source_parent_folder="A",
                file_size_bytes=128,
            )
            mixed_new_candidate = CandidateFile(
                volume_label="MIC 2",
                volume_root=tmp_path / "Volumes" / "MIC 2",
                source_path=tmp_path / "Volumes" / "MIC 2" / "A" / "TX01_MIC001_20260315_130000.wav",
                source_parent_folder="A",
                file_size_bytes=256,
            )
            mirrored = MirrorOutcome(
                raw_path=recordings_root / "raw" / "MIC_02" / "A" / mixed_new_candidate.source_path.name,
                checksum="abc123",
                size_bytes=256,
                status="mirrored",
                source_file_id=1,
                warning_count=0,
            )
            duplicate_only_candidate.volume_root.mkdir(parents=True)
            mixed_new_candidate.volume_root.mkdir(parents=True)

            class OneRunLock:
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
                run_detached_child=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=OneRunLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [
                            duplicate_only_candidate,
                            mixed_duplicate_candidate,
                            mixed_new_candidate,
                        ],
                        [
                            duplicate_only_candidate,
                            mixed_duplicate_candidate,
                            mixed_new_candidate,
                        ],
                    ],
                ),
                mock.patch(
                    "micsync.cli.find_preexisting_raw_duplicate",
                    side_effect=[
                        recordings_root / "raw" / "MIC_01" / "A" / duplicate_only_candidate.source_path.name,
                        recordings_root / "raw" / "MIC_02" / "A" / mixed_duplicate_candidate.source_path.name,
                        None,
                        recordings_root / "raw" / "MIC_01" / "A" / duplicate_only_candidate.source_path.name,
                        recordings_root / "raw" / "MIC_02" / "A" / mixed_duplicate_candidate.source_path.name,
                        recordings_root / "raw" / "MIC_02" / "A" / mixed_new_candidate.source_path.name,
                    ],
                ),
                mock.patch("micsync.cli._pending_derivation_queue", return_value=[]),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=False),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored) as mirror_recording_to_raw,
                mock.patch("micsync.cli.eject_volume", return_value=EjectResult(ok=True, detail=None)) as eject_volume,
                redirect_stdout(stdout),
            ):
                result = run_import(args)

        self.assertEqual(result, 0)
        mirror_recording_to_raw.assert_called_once()
        self.assertIn("micSync duplicate preflight complete new=1 existing=2 duplicate_only_volumes=1", stdout.getvalue())
        self.assertIn("micSync duplicate preflight complete new=0 existing=3 duplicate_only_volumes=2", stdout.getvalue())
        self.assertIn("summary mirrored=1 derived=0 duplicate=2 rescan_existing=3 failed=0 warning=0", stdout.getvalue())
        self.assertEqual(
            [call.args[0] for call in eject_volume.call_args_list],
            [duplicate_only_candidate.volume_root, mixed_new_candidate.volume_root],
        )
        self.assertIn("ejected volume MIC 1", stdout.getvalue())
        self.assertIn("ejected volume MIC 2", stdout.getvalue())

    def test_zero_new_rescan_suppresses_mirror_start_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            volume = tmp_path / "Volumes" / "MIC 01"
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
                volume_label="MIC 01",
                volume_root=volume,
                source_path=volume / "A" / "TX01_MIC001_20260315_120000.wav",
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

            class OneRunLock:
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

            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=OneRunLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [candidate],
                        [candidate],
                    ],
                ),
                mock.patch(
                    "micsync.cli.find_preexisting_raw_duplicate",
                    side_effect=[
                        None,
                        recordings_root / "raw" / "MIC_01" / "A" / candidate.source_path.name,
                    ],
                ),
                mock.patch(
                    "micsync.cli._pending_derivation_queue",
                    side_effect=[
                        [(1, mirrored.raw_path, candidate.source_path.name, 0, 128)],
                        [],
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=True),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch("micsync.cli.derive_mirrored_recording", return_value=mock.Mock(warning_count=0)),
                mock.patch("micsync.cli.send_notification") as send_notification,
            ):
                result = run_import(
                    argparse.Namespace(
                        max_file_size_mb=None,
                        derived=None,
                        notify=None,
                        eject=None,
                        source_volume=[str(volume)],
                        stop=False,
                        run_detached_child=False,
                    )
                )

        self.assertEqual(result, 0)
        mirror_start_calls = [
            call for call in send_notification.call_args_list
            if call.kwargs.get("title") == "micSync mirror starting"
        ]
        self.assertEqual(len(mirror_start_calls), 1)

    def test_run_warns_after_capped_rescans_without_stability(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            volume = tmp_path / "Volumes" / "MIC 01"
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
                notify=False,
                eject=True,
            )
            candidate = CandidateFile(
                volume_label="MIC 01",
                volume_root=volume,
                source_path=volume / "A" / "TX01_MIC001_20260315_120000.wav",
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

            class UnstableLock:
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
                    return True

                def release(self) -> None:
                    return None

            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=UnstableLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[candidate]),
                mock.patch("micsync.cli.find_preexisting_raw_duplicate", return_value=None),
                mock.patch(
                    "micsync.cli._pending_derivation_queue",
                    return_value=[(1, mirrored.raw_path, candidate.source_path.name, 0, 128)],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=False),
                mock.patch("micsync.cli.send_notification"),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch("micsync.cli.derive_mirrored_recording", return_value=mock.Mock(warning_count=0)),
                mock.patch("micsync.cli.eject_volume", return_value=EjectResult(ok=True, detail=None)) as eject_volume,
                redirect_stdout(stdout),
            ):
                result = run_import(
                    argparse.Namespace(
                        max_file_size_mb=None,
                        notify=None,
                        eject=None,
                        source_volume=[str(volume)],
                        stop=False,
                        run_detached_child=False,
                    )
                )

        self.assertEqual(result, 0)
        self.assertEqual(eject_volume.call_count, 0)
        self.assertIn("micSync rescan cap reached", stdout.getvalue())

    def test_failed_eject_warns_and_reports_attached_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            volume = tmp_path / "Volumes" / "MIC 01"
            volume.mkdir(parents=True)
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
                eject=True,
            )
            candidate = CandidateFile(
                volume_label="MIC 01",
                volume_root=volume,
                source_path=volume / "A" / "TX01_MIC001_20260315_120000.wav",
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

            class OneRunLock:
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

            stdout = io.StringIO()
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=OneRunLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [candidate],
                        [],
                    ],
                ),
                mock.patch(
                    "micsync.cli.Catalog.fetch_pending_source_files_for_derivation",
                    side_effect=[
                        [
                            {
                                "id": 1,
                                "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
                                "source_filename": candidate.source_path.name,
                            }
                        ],
                        [],
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=True),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch("micsync.cli.derive_mirrored_recording", return_value=mock.Mock(warning_count=0)),
                mock.patch(
                    "micsync.cli.eject_volume",
                    return_value=EjectResult(ok=False, detail="Resource busy"),
                ),
                mock.patch("micsync.cli.build_completion_message", return_value="complete") as build_completion_message,
                mock.patch("micsync.cli.send_notification") as send_notification,
                redirect_stdout(stdout),
            ):
                result = run_import(
                    argparse.Namespace(
                        max_file_size_mb=None,
                        notify=None,
                        eject=None,
                        source_volume=[str(volume)],
                        stop=False,
                        run_detached_child=False,
                    )
                )

        self.assertEqual(result, 0)
        self.assertIn("failed to eject volume MIC 01", stdout.getvalue())
        self.assertIn("Resource busy", stdout.getvalue())
        self.assertIn("volume still attached MIC 01", stdout.getvalue())
        self.assertEqual(build_completion_message.call_args.kwargs["warning_count"], 1)
        self.assertEqual(build_completion_message.call_args.kwargs["ejected_volumes"], [])
        self.assertEqual(build_completion_message.call_args.kwargs["attached_volumes"], ["MIC 01"])
        self.assertEqual(
            [call.kwargs["title"] for call in send_notification.call_args_list],
            [
                "micSync mirror starting",
                "micSync eject warning",
                "micSync volume still attached",
                "micSync import complete with warnings",
            ],
        )

    def test_clipboard_warning_persists_anomaly_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            volume = tmp_path / "Volumes" / "MIC 01"
            volume.mkdir(parents=True)
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
                volume_label="MIC 01",
                volume_root=volume,
                source_path=volume / "A" / "TX01_MIC001_20260315_120000.wav",
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

            class OneRunLock:
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

            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=OneRunLock()),
                mock.patch(
                    "micsync.cli.scan_candidates",
                    side_effect=[
                        [candidate],
                        [],
                    ],
                ),
                mock.patch(
                    "micsync.cli.Catalog.fetch_pending_source_files_for_derivation",
                    side_effect=[
                        [
                            {
                                "id": 1,
                                "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
                                "source_filename": candidate.source_path.name,
                            }
                        ],
                        [],
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=False),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch("micsync.cli.derive_mirrored_recording", return_value=mock.Mock(warning_count=0)),
                mock.patch("micsync.cli.send_notification") as send_notification,
            ):
                result = run_import(
                    argparse.Namespace(
                        max_file_size_mb=None,
                        notify=None,
                        eject=None,
                        source_volume=[str(volume)],
                        stop=False,
                        run_detached_child=False,
                    )
                )

            catalog = Catalog(config.recordings_db_path)
            anomaly_rows = catalog.fetch_anomalies()

        self.assertEqual(result, 0)
        self.assertEqual(len(anomaly_rows), 1)
        self.assertEqual(anomaly_rows[0]["code"], "clipboard_failure")
        self.assertEqual(anomaly_rows[0]["severity"], "warning")
        self.assertEqual(anomaly_rows[0]["phase"], "mirror_start")
        self.assertIn(
            "micSync warning",
            [call.kwargs["title"] for call in send_notification.call_args_list],
        )

    def test_derive_failure_persists_anomaly_and_falls_back_to_log_path_when_console_open_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recordings_root = tmp_path / "recordings"
            volume = tmp_path / "Volumes" / "MIC 01"
            volume.mkdir(parents=True)
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
                volume_label="MIC 01",
                volume_root=volume,
                source_path=volume / "A" / "TX01_MIC001_20260315_120000.wav",
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

            class OneRunLock:
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

            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=OneRunLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[candidate]),
                mock.patch(
                    "micsync.cli.Catalog.fetch_pending_source_files_for_derivation",
                    return_value=[
                        {
                            "id": 1,
                            "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
                            "source_filename": candidate.source_path.name,
                        }
                    ],
                ),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.cli.copy_to_clipboard", return_value=True),
                mock.patch("micsync.cli.mirror_recording_to_raw", return_value=mirrored),
                mock.patch(
                    "micsync.cli.derive_mirrored_recording",
                    side_effect=RuntimeError("derive failed"),
                ),
                mock.patch("micsync.cli.open_log_in_console", return_value=False) as open_log_in_console,
                mock.patch("micsync.cli.send_notification") as send_notification,
            ):
                result = run_import(
                    argparse.Namespace(
                        max_file_size_mb=None,
                        notify=None,
                        eject=None,
                        source_volume=[str(volume)],
                        stop=False,
                        run_detached_child=False,
                    )
                )

            catalog = Catalog(config.recordings_db_path)
            anomaly_rows = catalog.fetch_anomalies()

        self.assertEqual(result, 1)
        self.assertEqual(len(anomaly_rows), 1)
        self.assertEqual(anomaly_rows[0]["code"], "derive_failed")
        self.assertEqual(anomaly_rows[0]["severity"], "fail")
        open_log_in_console.assert_called_once()
        failure_notifications = [
            call.kwargs["message"]
            for call in send_notification.call_args_list
            if call.kwargs["title"] == "micSync failure"
        ]
        self.assertEqual(len(failure_notifications), 1)
        self.assertIn(str(config.runtime_root / "logs" / "runs.log"), failure_notifications[0])

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
                run_detached_child=False,
            )
            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[candidate]),
                mock.patch(
                    "micsync.cli.Catalog.fetch_pending_source_files_for_derivation",
                    return_value=[
                        {
                            "id": 1,
                            "raw_relative_path": "raw/MIC_01/A/TX01_MIC001_20260315_120000.wav",
                            "source_filename": candidate.source_path.name,
                        }
                    ],
                ),
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
        self.assertIn("summary mirrored=1 derived=0 duplicate=0 rescan_existing=0 failed=1", log_contents)

    def test_derive_stage_processes_pending_rows_in_chronological_order(self) -> None:
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
                notify=False,
                eject=False,
            )
            source_root = tmp_path / "Volumes" / "MIC 01" / "A"
            source_root.mkdir(parents=True)
            late_file = source_root / "TX01_MIC005_20260308_153556_orig.wav"
            late_file.write_bytes(b"late")
            candidate = CandidateFile(
                volume_label="MIC 01",
                volume_root=tmp_path / "Volumes" / "MIC 01",
                source_path=late_file,
                source_parent_folder="A",
                file_size_bytes=late_file.stat().st_size,
            )
            catalog = Catalog(config.recordings_db_path)
            earlier_id = catalog.upsert_source_file(
                source_key="raw/MIC_01/A/TX01_MIC004_20260308_150556_orig.wav",
                segment_id=None,
                source_volume_label="MIC 01",
                source_volume_identifier="MIC 01",
                source_mount_path=str(candidate.volume_root),
                source_parent_folder="A",
                source_filename="TX01_MIC004_20260308_150556_orig.wav",
                source_relative_path="A/TX01_MIC004_20260308_150556_orig.wav",
                physical_mic_id=1,
                raw_relative_path="raw/MIC_01/A/TX01_MIC004_20260308_150556_orig.wav",
                source_size_bytes=100,
                source_checksum="earlier-checksum",
                recording_start_at="2026-03-08T15:05:56",
                recording_end_at=None,
                duration_ms=1800000,
                variant="orig",
                mirror_status="mirrored",
                first_seen_at="2026-03-08T15:06:00",
                last_attempted_at="2026-03-08T15:06:00",
                mirrored_at="2026-03-08T15:06:00",
                error_phase=None,
                error_detail=None,
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
                run_detached_child=False,
            )
            derive_calls: list[int] = []

            def derive_side_effect(**kwargs):
                source_file_id = kwargs["source_file_id"]
                derive_calls.append(source_file_id)
                take_id = catalog.upsert_take(
                    take_key=f"take-{source_file_id}",
                    take_start_at="2026-03-08T15:00:00",
                    take_end_at=None,
                    tx_slot="TX01",
                    physical_mic_id=1,
                    source_parent_folder="A",
                )
                segment_id = catalog.upsert_segment(
                    take_id=take_id,
                    segment_key=f"segment-{source_file_id}",
                    segment_start_at="2026-03-08T15:00:00",
                    segment_end_at=None,
                    tx_slot="TX01",
                    mic_sequence=f"MIC{source_file_id:03d}",
                    physical_mic_id=1,
                    source_parent_folder="A",
                    duration_ms=1800000,
                    first_seen_at="2026-03-08T15:00:00",
                    last_attempted_at="2026-03-08T15:00:00",
                    completed_at="2026-03-08T15:00:00",
                )
                catalog.assign_source_file_to_segment(
                    source_file_id=source_file_id,
                    segment_id=segment_id,
                )
                return mock.Mock(warning_count=0)

            with (
                mock.patch("micsync.cli._load_config", return_value=config),
                mock.patch("micsync.cli.LockManager", return_value=FakeLock()),
                mock.patch("micsync.cli.scan_candidates", return_value=[candidate]),
                mock.patch("micsync.cli.build_stop_command", return_value="micSync --stop"),
                mock.patch("micsync.importer.read_duration_ms", return_value=1800000),
                mock.patch("micsync.cli.derive_mirrored_recording", side_effect=derive_side_effect),
            ):
                result = run_import(args)

            self.assertEqual(result, 0)
            self.assertEqual(derive_calls[0], earlier_id)
            self.assertEqual(len(derive_calls), 2)
            later_row = catalog.fetch_source_file(derive_calls[1])
            self.assertEqual(later_row["source_filename"], late_file.name)
