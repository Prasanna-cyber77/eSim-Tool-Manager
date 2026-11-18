import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from esim_tool_manager import cli, settings
from esim_tool_manager.packages import PackageOperationError, Plan, WINGET_HASH_MISMATCH
from esim_tool_manager.portable import NGSPICE, PortablePlan
from esim_tool_manager.runtime import Host, ManagerError


class CLITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.host = Host("windows", "10.0.22631", "amd64")
        self.root = self.base / "eSim Installed"
        app = self.root / "src/frontEnd/Application.py"
        app.parent.mkdir(parents=True)
        app.touch()
        (self.root / "VERSION").write_text("2.6\n", encoding="utf-8")
        self.config_path = self.base / "eSimToolManager/settings.json"
        self.log_path = self.base / "eSimToolManager/operations.jsonl"
        self.enterContext(patch.object(cli.Host, "detect", return_value=self.host))
        self.enterContext(patch.object(cli.Path, "home", return_value=self.base / "home"))
        self.enterContext(patch.dict(os.environ, {"LOCALAPPDATA": str(self.base), "PATH": "",
                                                "ProgramFiles": str(self.base / "Program Files")}, clear=True))

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cli.main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_preview_and_decline_never_execute_package_change(self):
        manager = Mock()
        manager.plan.return_value = Plan("install", "kicad", "KiCad.KiCad", None, "9.0.3", "winget",
                                         ("winget", "install"), "available")
        with patch.object(cli, "PackageManager", return_value=manager), patch.object(cli, "confirm", return_value=False) as confirm:
            code, output, _ = self.invoke("install", "kicad", "--dry-run")
            self.assertEqual(code, 0)
            self.assertIn("Planned command", output)
            confirm.assert_not_called()
            code, output, _ = self.invoke("install", "kicad", "--apply")
            self.assertEqual(code, 3)
            self.assertIn("Cancelled", output)
            manager.execute.assert_not_called()

    def test_portable_preview_and_refusal_do_not_download(self):
        manager = Mock()
        manager.plan.return_value = PortablePlan(NGSPICE, self.base / "ngspice", self.base / "tar.exe", "available")
        with patch.object(cli, "PortableManager", return_value=manager), patch.object(cli, "confirm", return_value=False) as confirm:
            code, output, _ = self.invoke("--json", "install", "ngspice", "--dry-run")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["expected_sha256"], NGSPICE.sha256)
            confirm.assert_not_called()
            self.assertEqual(self.invoke("install", "ngspice", "--apply")[0], 3)
        manager.execute.assert_not_called()

    def test_portable_integrity_failure_never_logs_or_prints_success(self):
        manager = Mock()
        manager.plan.return_value = PortablePlan(NGSPICE, self.base / "ngspice", self.base / "tar.exe", "available")
        manager.execute.side_effect = ManagerError("Portable archive integrity verification failed.")
        with patch.object(cli, "PortableManager", return_value=manager), patch.object(cli, "confirm", return_value=True):
            code, output, error = self.invoke("install", "ngspice", "--apply")
        self.assertEqual(code, 2)
        self.assertNotIn("Verified portable", output)
        self.assertIn("integrity verification failed", error)
        events = [json.loads(line)["event"] for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertIn("portable_failed", events)
        self.assertNotIn("portable_verified", events)

    def test_confirmed_upgrade_dispatches_once_and_logs_verification(self):
        manager = Mock()
        manager.plan.return_value = Plan("upgrade", "kicad", "KiCad.KiCad", "9.0.3", "9.0.8", "winget",
                                         ("winget", "upgrade"), "available")
        manager.execute.return_value = {"package_version": "9.0.8", "executable": self.root / "kicad-cli.exe",
                                        "executable_version": "9.0.8"}
        with patch.object(cli, "PackageManager", return_value=manager), patch.object(cli, "confirm", return_value=True):
            status, output, _ = self.invoke("upgrade", "kicad", "--apply")
        manager.execute.assert_called_once()
        self.assertEqual(status, 0)
        self.assertIn("9.0.8", output)
        events = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event"] for event in events], ["package_plan", "package_started", "package_verified"])

    def test_apply_refuses_non_interactive_input(self):
        manager = Mock()
        manager.plan.return_value = Plan("install", "kicad", "KiCad.KiCad", None, "9.0.3", "winget",
                                         ("winget", "install"), "available")
        with patch.object(cli, "PackageManager", return_value=manager), patch("sys.stdin", io.StringIO("yes\n")):
            code, _, failure = self.invoke("install", "kicad", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", failure)
        manager.execute.assert_not_called()

    def test_hash_failure_logs_native_code_and_unchanged_state(self):
        manager = Mock()
        plan = Plan("install", "kicad", "KiCad.KiCad", None, "10.0.8", "winget", ("winget", "install"),
                    "available", installer_sha256="b" * 64, latest_available="10.0.8")
        manager.plan.return_value = plan
        native = subprocess.CompletedProcess(plan.command, WINGET_HASH_MISMATCH, None, None)
        manager.execute.side_effect = PackageOperationError(plan, "absent", windows=True, result=native)
        with patch.object(cli, "PackageManager", return_value=manager), patch.object(cli, "confirm", return_value=True):
            code, output, error = self.invoke("install", "kicad", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("Installed: none | Latest available: 10.0.8 | Selected: 10.0.8", output)
        self.assertIn("Installer integrity verification failed", error)
        self.assertIn("2316632081 (0x8A150011)", error)
        self.assertNotIn("Verified", output)
        events = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        failure = next(event for event in events if event["event"] == "package_failed")
        self.assertEqual(failure["returncode"], 2316632081)
        self.assertEqual(failure["installation_state"], "unchanged")
        self.assertEqual(failure["installer_sha256"], "b" * 64)
        self.assertIn("winget --info", failure["native_log_hint"])
        self.assertNotIn("package_verified", [event["event"] for event in events])
        with patch.object(cli, "PackageManager", return_value=manager), patch.object(cli, "confirm", return_value=True):
            code, output, error = self.invoke("install", "kicad", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("Repeated integrity failures recorded", error)
        self.assertEqual(manager.execute.call_count, 2)  # Two approvals, one invocation per approval.

    def test_diagnose_json_reads_logs_without_invoking_package_manager(self):
        directory = self.base / "copied winget logs"
        directory.mkdir()
        source = directory / "WinGet-2026-11-17-21-49-27.log"
        source.write_text(
            "WinGet invoked with arguments: 'install' '--id' 'KiCad.KiCad' '--source' 'winget'\n"
            "Found one app. App id: KiCad.KiCad App name: KiCad\n"
            "Manifest fields: Name [KiCad], Version [10.0.8]\n"
            "[CORE] DeliveryOptimization downloading from url: https://example.invalid/kicad.exe\n"
            "[CORE] WinINet downloading from url: https://example.invalid/kicad.exe\n"
            f"Package hash verification failed. SHA256 in manifest [{'a' * 64}] "
            f"does not match download [{'b' * 64}] with file size [915406848]\n"
            "Terminating context: 0x8a150011 at DownloadFlow.cpp:287\n", encoding="utf-8",
        )
        with patch.object(cli, "PackageManager") as manager, patch.object(cli, "event_logger") as logger:
            code, output, _ = self.invoke("--json", "diagnose", "kicad", "--winget-log-dir", str(directory))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {
            "package": "KiCad.KiCad", "selected_version": "10.0.8", "package_source": "winget",
            "installer_url": "https://example.invalid/kicad.exe", "expected_sha256": "a" * 64,
            "actual_sha256": "b" * 64, "file_size_bytes": 915406848, "downloader": "WinINet",
            "failure_category": "integrity_verification_failure", "returncode": 2316632081, "log": str(source),
            "recorded_integrity_failures_at_least": 1, "recovery_actions": [
                "Keep hash verification enabled. Compare the recorded URL and expected digest with the publisher's release asset.",
                "If the digest agrees, investigate the transfer on a trusted connection before approving another attempt.",
            ],
        })
        manager.assert_not_called()
        logger.assert_not_called()

    def test_diagnose_filters_version_and_uses_rotated_manager_log(self):
        self.log_path.parent.mkdir()
        self.log_path.with_suffix(".jsonl.1").write_text("unparseable line\n" + json.dumps({
            "event": "package_failed", "package": "KiCad.KiCad", "target": "9.0.3", "source": "winget",
            "installer_sha256": "a" * 64, "category": "integrity_verification_failure", "returncode": WINGET_HASH_MISMATCH,
        }) + "\n", encoding="utf-8")
        code, output, _ = self.invoke("diagnose", "kicad", "--version", "9.0.3")
        self.assertEqual(code, 0)
        self.assertIn("Selected version: 9.0.3", output)
        self.assertIn("Actual SHA-256: not recorded", output)
        self.assertIn("Expected SHA-256: " + "a" * 64, output)
        code, output, _ = self.invoke("diagnose", "kicad", "--version", "10.0.8")
        self.assertEqual(code, 1)
        self.assertIn("Failure category: no_recorded_failure", output)

    def test_config_updates_need_approval_and_keep_existing_fields(self):
        self.assertEqual(self.invoke("config", "set", "esim_root", str(self.root))[0], 0)
        before = self.config_path.read_bytes()
        override = self.base / "ngspice.exe"
        override.touch()
        override.chmod(0o755)
        with patch.object(cli, "confirm", return_value=False):
            self.assertEqual(self.invoke("config", "set", "ESIM_NGSPICE", str(override))[0], 3)
        self.assertEqual(self.config_path.read_bytes(), before)
        with patch.object(cli, "confirm", return_value=True):
            self.assertEqual(self.invoke("config", "set", "ESIM_NGSPICE", str(override))[0], 0)
        code, output, _ = self.invoke("--json", "config", "show")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["settings"], {"esim_root": str(self.root), "ESIM_NGSPICE": str(override)})
        self.assertFalse((self.base / "home/.nghdl/config.ini").exists())

    def test_invalid_configuration_remains_untouched(self):
        self.config_path.parent.mkdir()
        self.config_path.write_text("broken JSON", encoding="utf-8")
        code, _, failure = self.invoke("config", "set", "esim_root", str(self.root))
        self.assertEqual(code, 2)
        self.assertIn("Cannot read manager settings", failure)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), "broken JSON")

    def test_save_refuses_to_overwrite_concurrent_change(self):
        initial = settings.load(self.config_path)
        settings.save(self.config_path, {"esim_root": str(self.root)}, initial)
        with self.assertRaisesRegex(ManagerError, "Settings changed"):
            settings.save(self.config_path, {}, initial)
        self.assertEqual(settings.load(self.config_path)[0], {"esim_root": str(self.root)})

    def test_list_and_doctor_json_communicate_missing_dependencies(self):
        code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "list")
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(output)["ngspice"]["resolution"]["selected"])
        code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "doctor", "--flow", "dcosim")
        self.assertEqual(code, 1)
        data = json.loads(output)
        self.assertEqual(list(data["flows"]), ["dcosim"])
        self.assertEqual(list(data["tools"]), ["ngspice", "iverilog", "vvp"])
        self.assertEqual(data["flows"]["dcosim"]["status"], "missing")
        self.assertEqual(data["flows"]["dcosim"]["compatibility"], "unverified")

    def test_doctor_explains_source_checkout_and_shows_search_paths_on_request(self):
        (self.root / ".git").mkdir()
        requested = self.root / ".." / self.root.name
        args = ("--esim-root", str(requested), "doctor")
        code, output, _ = self.invoke(*args)
        self.assertEqual(code, 1)
        self.assertIn(f"eSim root: {self.root} |", output)
        self.assertIn("source checkout has no staged Windows tools", output)
        self.assertIn("(dcosim) | missing | Compatibility: unverified", output)
        self.assertNotIn("Searched:", output)
        self.assertIn("Searched:", self.invoke(*args, "--verbose")[1])

    def test_focused_text_reports_only_netlisting_dependencies(self):
        code, output, _ = self.invoke("--esim-root", str(self.root), "doctor", "--flow", "netlisting")
        self.assertEqual(code, 1)
        self.assertIn("Basic netlisting (netlisting) | missing | Compatibility: unverified", output)
        self.assertIn("kicad: missing | Version: unknown | Path: none", output)
        self.assertIn("install kicad --dry-run", output)
        for unrelated in ("ngspice", "iverilog", "ghdl", "MSYS2", "Searched:"):
            self.assertNotIn(unrelated, output)

    def test_invalid_esim_root_is_reported(self):
        self.root.joinpath("VERSION").unlink()
        for path, message in ((self.base / "missing eSim", "Directory does not exist"),
                              (self.root, "has no eSim VERSION")):
            with self.subTest(path=path):
                code, _, error = self.invoke("--esim-root", str(path), "doctor")
                self.assertEqual(code, 2)
                self.assertIn(message, error)
