import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from esim_tool_manager import cli, verification
from esim_tool_manager.discovery import Candidate, Discovery, Resolution, discover_root, find_root
from esim_tool_manager.environment import Change, prepare, render_snippet
from esim_tool_manager.health import Probe, doctor
from esim_tool_manager.portable import NGSPICE, RECEIPT, ManagedInspection, Release, inspect_managed, sha256
from esim_tool_manager.runtime import Host, ManagerError
from esim_tool_manager.simulation import CIRCUIT_ID, SimulationResult, analog_smoke_test


def make_root(base, name="eSim"):
    root = Path(base) / name
    (root / "src/frontEnd").mkdir(parents=True)
    (root / "VERSION").write_text("2.6\n", encoding="utf-8")
    (root / "src/frontEnd/Application.py").touch()
    return root


def executable(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ fake console image")
    path.chmod(0o755)
    return path


def result(executable_path, *, verified=True, observed=1.0, exit_code=0, reasons=()):
    return SimulationResult(
        Path(executable_path), Path("workspace"), CIRCUIT_ID, ("ngspice", "-b", "circuit.cir"),
        exit_code, "", "", 0.25, observed, 1.0, 1e-6,
        "verified" if verified else "failed", verified, tuple(reasons),
    )


class RootDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.host = Host("windows", "10.0.22631", "amd64")
        self.env = {"SystemDrive": str(self.base / "system-drive")}

    def test_explicit_root_wins_and_is_canonical(self):
        root = make_root(self.base)
        selection = discover_root(self.host, self.env, explicit=root / ".", start=self.base)
        self.assertEqual(selection.path, root.resolve())
        self.assertEqual(selection.method, "explicit --esim-root")

    def test_configured_root_and_environment_precedence(self):
        configured = make_root(self.base / "configured", "eSim Installed")
        environment = make_root(self.base / "environment", "eSim Home")
        selection = discover_root(self.host, {"ESIM_HOME": str(environment)},
                                  configured=configured, start=self.base)
        self.assertEqual(selection.path, configured.resolve())
        self.assertEqual(selection.method, "manager setting esim_root")
        selection = discover_root(self.host, {"ESIM_HOME": str(environment)}, start=self.base)
        self.assertEqual(selection.path, environment.resolve())
        self.assertEqual(selection.method, "ESIM_HOME environment variable")

    def test_current_directory_then_unambiguous_sibling(self):
        root = make_root(self.base / "search")
        selection = discover_root(self.host, self.env, start=root)
        self.assertEqual(selection.path, root.resolve())
        self.assertEqual(selection.method, "current working directory")

        project = self.base / "search" / "manager"
        project.mkdir()
        working = self.base / "work"
        working.mkdir()
        selection = discover_root(self.host, self.env, start=working, project_root=project)
        self.assertEqual(selection.path, root.resolve())
        self.assertEqual(selection.method, "sibling of the tool-manager directory")

    def test_ambiguous_siblings_are_not_guessed(self):
        make_root(self.base, "alpha")
        make_root(self.base, "beta")
        project = self.base / "manager"
        project.mkdir()
        selection = discover_root(self.host, self.env, start=project, project_root=project)
        self.assertIsNone(selection.path)
        self.assertEqual(selection.method, "ambiguous sibling candidates")
        self.assertIn("--esim-root", selection.warning)
        self.assertIsNone(find_root(self.host, self.env, start=project, project_root=project))

    def test_documented_windows_fallback_and_undetected(self):
        make_root(self.base / "system-drive" / "FOSSEE")
        isolated = self.base / "isolated"
        isolated.mkdir()
        selection = discover_root(self.host, self.env, start=isolated, project_root=isolated)
        self.assertEqual(selection.path, (self.base / "system-drive/FOSSEE/eSim").resolve())
        self.assertEqual(selection.method, "documented Windows installation path")

        (self.base / "system-drive/FOSSEE/eSim/VERSION").unlink()
        self.assertIsNone(discover_root(self.host, self.env, start=isolated, project_root=isolated).path)

    def test_invalid_explicit_root_is_rejected(self):
        for path, message in ((self.base / "absent", "Directory does not exist"),
                              (make_root(self.base), None)):
            if message is None:
                (path / "VERSION").unlink()
                message = "has no eSim VERSION"
            with self.subTest(path=path):
                with self.assertRaisesRegex(ManagerError, message):
                    discover_root(self.host, self.env, explicit=path)


class EnvironmentPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.host = Host("windows", "10.0.22631", "amd64")
        self.home = self.base / "home"
        self.prefix = self.base / "portable store" / "O'Brien" / "ngspice" / "47"
        self.binary = executable(self.prefix / "bin/ngspice_con.exe")
        (self.prefix / "share/ngspice").mkdir(parents=True)
        self.candidate = Candidate(self.binary, "managed Ngspice archive", False)

    def discovery(self, env):
        return Discovery(self.host, env, self.home, None, "python")

    def test_sets_child_environment_without_mutating_parent(self):
        before = dict(os.environ)
        plan = prepare(self.discovery({"PATH": r"C:\Windows"}), self.candidate, "47")
        self.assertEqual(os.environ, before)
        self.assertEqual(plan.environment["ESIM_NGSPICE"], str(self.binary.resolve()))
        self.assertTrue(plan.environment["PATH"].startswith(str(self.binary.parent.resolve()) + os.pathsep))
        self.assertEqual(plan.environment["SPICE_LIB_DIR"], str(self.prefix / "share/ngspice"))
        self.assertEqual([change.variable for change in plan.changes],
                         ["ESIM_NGSPICE", "PATH", "SPICE_LIB_DIR"])
        self.assertIn("$env:ESIM_NGSPICE = ", plan.snippet)
        self.assertIn("[IO.Path]::PathSeparator", plan.snippet)
        self.assertEqual(self.discovery({"PATH": ""}).env, {"PATH": ""})

    def test_second_preparation_is_idempotent(self):
        first = prepare(self.discovery({"PATH": r"C:\Windows"}), self.candidate, "47")
        second = prepare(self.discovery(first.environment), self.candidate, "47")
        self.assertEqual(second.changes, ())
        self.assertIn("already points", " ".join(second.notes))
        self.assertIn("already on PATH", " ".join(second.notes))
        self.assertEqual(second.snippet, "# Environment already prepared; no changes required.")

    def test_preserves_unrelated_values_and_reports_conflicts(self):
        existing_lib = self.base / "custom lib"
        plan = prepare(self.discovery({"ESIM_NGSPICE": str(self.prefix / "bin/NGSPICE_CON.EXE"),
                                       "SPICE_LIB_DIR": str(existing_lib)}),
                       self.candidate, "47")
        self.assertIn("Existing SPICE_LIB_DIR is preserved", " ".join(plan.notes))
        self.assertNotIn("SPICE_LIB_DIR", [change.variable for change in plan.changes])
        self.assertNotIn("ESIM_NGSPICE", [change.variable for change in plan.changes])

    def test_shell_snippets_quote_spaces_and_special_characters(self):
        changes = (Change("ESIM_NGSPICE", "set", None, str(self.binary)),
                   Change("PATH", "prepend", "old", str(self.binary.parent)))
        powershell = render_snippet(changes, "powershell")
        self.assertIn("$env:ESIM_NGSPICE = '", powershell)
        self.assertIn("O''Brien", powershell)
        cmd = render_snippet(changes, "cmd")
        self.assertIn('set "ESIM_NGSPICE=', cmd)
        self.assertIn('set "PATH=', cmd)
        bash = render_snippet(changes, "bash")
        self.assertIn('export ESIM_NGSPICE="', bash)
        self.assertIn(":$PATH", bash)

    def test_snippet_without_changes_is_an_instruction_comment(self):
        self.assertEqual(render_snippet((), "cmd"), "# Environment already prepared; no changes required.")


class ManagedInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.directory = self.base / "ngspice/47"
        self.binary = executable(self.directory / "bin/ngspice_con.exe")
        (self.directory / "share").mkdir()
        (self.directory / "share/readme.txt").write_text("fixture", encoding="utf-8")
        self.release = Release("47", "https://example.invalid/archive.7z", "a" * 64, "test")
        files = {path.relative_to(self.directory).as_posix(): sha256(path)
                 for path in self.directory.rglob("*") if path.is_file()}
        self.receipt = {"version": "47", "archive_sha256": "a" * 64, "files": files}
        (self.directory / RECEIPT).write_text(json.dumps(self.receipt), encoding="utf-8")

    def test_valid_receipt_verifies_every_file(self):
        inspection = inspect_managed(self.directory, self.release)
        self.assertTrue(inspection.verified)
        self.assertEqual(inspection.binary, self.binary)
        self.assertEqual(inspection.files_checked, 2)
        self.assertEqual(inspection.problems, ())

    def test_modified_missing_extra_and_corrupt_files_are_all_rejected(self):
        (self.directory / "share/readme.txt").write_text("changed", encoding="utf-8")
        inspection = inspect_managed(self.directory, self.release)
        self.assertFalse(inspection.verified)
        self.assertIsNone(inspection.binary)
        self.assertIn("share/readme.txt", inspection.modified_files)

        self.receipt["files"].pop("share/readme.txt")
        (self.directory / RECEIPT).write_text(json.dumps(self.receipt), encoding="utf-8")
        inspection = inspect_managed(self.directory, self.release)
        self.assertIn("share/readme.txt", inspection.extra_files)

        (self.directory / "unrecorded.bin").write_bytes(b"x")
        inspection = inspect_managed(self.directory, self.release)
        self.assertIn("unrecorded.bin", inspection.extra_files)

        (self.directory / RECEIPT).write_text("not json", encoding="utf-8")
        inspection = inspect_managed(self.directory, self.release)
        self.assertTrue(inspection.receipt_present)
        self.assertFalse(inspection.receipt_valid)
        self.assertIn("cannot be read", " ".join(inspection.problems))

    def test_receipt_version_and_catalog_digest_must_match(self):
        self.receipt["version"] = "46"
        self.receipt["archive_sha256"] = "b" * 64
        (self.directory / RECEIPT).write_text(json.dumps(self.receipt), encoding="utf-8")
        inspection = inspect_managed(self.directory, self.release)
        self.assertFalse(inspection.verified)
        self.assertIn("version '46'", " ".join(inspection.problems))
        self.assertIn("archive digest", " ".join(inspection.problems))

    def test_linked_receipt_is_rejected_without_reading_it(self):
        receipt = self.directory / RECEIPT
        with patch("esim_tool_manager.portable.is_link", side_effect=lambda path: Path(path) == receipt):
            inspection = inspect_managed(self.directory, self.release)
        self.assertFalse(inspection.verified)
        self.assertTrue(inspection.receipt_present)
        self.assertFalse(inspection.receipt_valid)
        self.assertIn("receipt is a filesystem link", " ".join(inspection.problems))


class VerificationReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.binary = executable(self.base / "ngspice/bin/ngspice_con.exe")
        self.path = verification.path_for(self.base / "settings.json")

    def test_round_trip_and_atomic_replacement(self):
        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        verification.save(self.path, receipt)
        self.assertEqual(verification.load(self.path), receipt)
        self.assertEqual(list(self.base.glob("*.tmp")), [])
        verified, detail = verification.check(receipt, self.binary, "47")
        self.assertTrue(verified)
        self.assertIn("v(out) = 1.0", detail)

    def test_corrupt_or_unknown_schema_is_ignored(self):
        self.path.write_text("broken", encoding="utf-8")
        self.assertIsNone(verification.load(self.path))
        self.path.write_text(json.dumps({"schema": 99}), encoding="utf-8")
        self.assertIsNone(verification.load(self.path))

    def test_changed_executable_version_or_status_invalidates_receipt(self):
        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        self.binary.write_bytes(b"replaced")
        self.assertFalse(verification.check(receipt, self.binary, "47")[0])

        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        self.assertFalse(verification.check(receipt, self.binary, "48")[0])
        self.assertIn("reports 48", verification.check(receipt, self.binary, "48")[1])

        other = executable(self.base / "other/bin/ngspice_con.exe")
        self.assertIn("different simulator", verification.check(receipt, other, "47")[1])
        self.assertFalse(verification.check(None, self.binary, "47")[0])
        failed = dict(receipt, status="failed")
        self.assertFalse(verification.check(failed, self.binary, "47")[0])
        other_tool = dict(receipt, tool="kicad")
        self.assertIn("another tool", verification.check(other_tool, self.binary, "47")[1])
        missing = dict(receipt)
        missing.pop("executable_sha256")
        self.assertFalse(verification.check(missing, self.binary, "47")[0])
        outside = dict(receipt, observed=2.0)
        self.assertIn("outside", verification.check(outside, self.binary, "47")[1])

    def test_non_finite_negative_tolerance_and_failed_exit_are_rejected(self):
        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        for changes, message in (
            ({"observed": float("nan")}, "invalid result evidence"),
            ({"expected": float("inf")}, "invalid result evidence"),
            ({"tolerance": -1.0}, "invalid result evidence"),
            ({"simulation_exit_code": 1}, "did not exit successfully"),
        ):
            with self.subTest(changes=changes):
                self.assertIn(message, verification.check(dict(receipt, **changes), self.binary, "47")[1])

    def test_stale_summary_is_a_compact_evidence_view(self):
        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        summary = verification.stale_summary(receipt)
        self.assertEqual(summary["circuit"], CIRCUIT_ID)
        self.assertNotIn("executable_sha256", summary)

    def test_manipulated_receipt_values_are_rejected(self):
        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        # Manipulated expected value
        manipulated_expected = dict(receipt, expected=2.0, observed=2.0)
        verified, message = verification.check(manipulated_expected, self.binary, "47")
        self.assertFalse(verified)
        self.assertIn("untrusted expected value", message)

        # Manipulated tolerance
        manipulated_tolerance = dict(receipt, tolerance=10.0, observed=5.0)
        verified, message = verification.check(manipulated_tolerance, self.binary, "47")
        self.assertFalse(verified)
        self.assertIn("untrusted tolerance", message)

        # Manipulated circuit identifier
        manipulated_circuit = dict(receipt, circuit="foreign-circuit")
        verified, message = verification.check(manipulated_circuit, self.binary, "47")
        self.assertFalse(verified)
        self.assertIn("untrusted circuit identifier", message)

        # Manipulated binary hash
        manipulated_hash = dict(receipt, executable_sha256="0" * 64)
        verified, message = verification.check(manipulated_hash, self.binary, "47")
        self.assertFalse(verified)
        self.assertIn("simulator executable changed", message)


class SimulationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.binary = executable(self.base / "spaced folder/ngspice_con.exe")

    def test_success_runs_in_isolated_workspace_and_checks_numeric_output(self):
        captured = {}

        def runner(argv, **kwargs):
            workspace = Path(kwargs["cwd"])
            captured["argv"] = argv
            captured["workspace"] = workspace
            captured["netlist"] = (workspace / "voltage-divider.cir").read_text(encoding="ascii")
            return subprocess.CompletedProcess(argv, 0, "\nNo. of Data Rows : 1\nv(out) = 1.000000e+00\n", "")

        outcome = analog_smoke_test(self.binary, {"PATH": "x"}, runner=runner, timeout=7)
        self.assertTrue(outcome.verified)
        self.assertEqual(outcome.observed, 1.0)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(captured["argv"][-2:], ["-b", "voltage-divider.cir"])
        self.assertIn("V1 in 0 dc 3", captured["netlist"])
        self.assertFalse(captured["workspace"].exists())
        self.assertFalse(outcome.workspace.exists())

    def test_wrong_number_nonzero_exit_and_error_text_are_failures(self):
        cases = ((0, "v(out) = 2.000000e+00\n", None),
                 (1, "v(out) = 1.000000e+00\n", None),
                 (0, "v(out) = 1.000000e+00\nError: singular matrix\n", None))
        for code, output, _ in cases:
            with self.subTest(code=code, output=output):
                runner = Mock(return_value=subprocess.CompletedProcess([], code, output, ""))
                outcome = analog_smoke_test(self.binary, {}, runner=runner)
                self.assertFalse(outcome.verified)
                self.assertTrue(outcome.reasons)

    def test_timeout_missing_value_and_missing_executable_are_reported(self):
        with self.assertRaisesRegex(ManagerError, "does not exist"):
            analog_smoke_test(self.base / "absent.exe", {})
        runner = Mock(side_effect=ManagerError("simulation timed out"))
        outcome = analog_smoke_test(self.binary, {}, runner=runner)
        self.assertEqual(outcome.status, "error")
        self.assertIn("timed out", " ".join(outcome.reasons))

        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "no value\n", ""))
        outcome = analog_smoke_test(self.binary, {}, runner=runner)
        self.assertIsNone(outcome.observed)
        self.assertIn("No numeric", " ".join(outcome.reasons))

    def test_captures_are_bounded_and_keep_the_value(self):
        output = "x" * 9000 + "\nv(out) = 1.0\n"
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, output, ""))
        outcome = analog_smoke_test(self.binary, {}, runner=runner)
        self.assertTrue(outcome.verified)
        self.assertLessEqual(len(outcome.stdout), 8000)
        self.assertTrue(outcome.stdout.endswith("v(out) = 1.0\n"))

    def test_unexpected_workspace_artifacts_are_recorded_and_rejected(self):
        def runner(argv, **kwargs):
            Path(kwargs["cwd"], "unexpected.log").write_text("unexpected", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "v(out) = 1.0\n", "")

        outcome = analog_smoke_test(self.binary, {}, runner=runner)
        self.assertFalse(outcome.verified)
        self.assertEqual(outcome.artifacts, ("unexpected.log", "voltage-divider.cir"))
        self.assertIn("Unexpected simulation artifacts", " ".join(outcome.reasons))


class ManagedHealthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = self.base / "local/eSimToolManager/tools"
        self.directory = self.store / "ngspice/47"
        self.binary = executable(self.directory / "bin/ngspice_con.exe")
        (self.directory / "share/ngspice").mkdir(parents=True)
        (self.directory / "lib/ngspice").mkdir(parents=True)
        (self.directory / "lib/ngspice/digital.cm").touch()
        files = {path.relative_to(self.directory).as_posix(): sha256(path)
                 for path in self.directory.rglob("*") if path.is_file()}
        (self.directory / RECEIPT).write_text(json.dumps({
            "version": "47", "archive_sha256": NGSPICE.sha256, "files": files,
        }), encoding="utf-8")
        self.host = Host("windows", "10.0.22631", "amd64")
        self.home = self.base / "home"

    def discovery(self, env):
        return Discovery(self.host, env, self.home, None, "python", portable_store=self.store)

    def test_override_of_managed_binary_keeps_standard_build_classification(self):
        env = {"PATH": "", "ESIM_NGSPICE": str(self.binary)}
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "ngspice-47\n", ""))
        with patch("esim_tool_manager.health.windows_console_image", return_value=True):
            report = doctor(self.discovery(env), "simulation", runner=runner)
        self.assertEqual(report["ngspice"]["build_kind"], "standard")
        self.assertEqual(report["ngspice"]["flavor"], "standard Ngspice portable release")
        self.assertEqual(report["flows"]["simulation"]["status"], "ready")
        self.assertEqual(report["flows"]["simulation"]["compatibility"], "unverified")
        self.assertEqual(report["ngspice"]["runtime_compatibility"], "unverified")

        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        with patch("esim_tool_manager.health.windows_console_image", return_value=True):
            report = doctor(self.discovery(env), "simulation", runner=runner, verification=receipt)
        self.assertEqual(report["flows"]["simulation"]["compatibility"], "verified")
        self.assertEqual(report["ngspice"]["runtime_compatibility"], "verified")

    def test_runtime_receipt_requires_a_usable_version_probe(self):
        env = {"PATH": "", "ESIM_NGSPICE": str(self.binary)}
        receipt = verification.build_receipt(result(self.binary), self.binary, "47")
        runner = Mock(return_value=subprocess.CompletedProcess([], 1, "", "probe failed"))
        with patch("esim_tool_manager.health.windows_console_image", return_value=True):
            report = doctor(self.discovery(env), "simulation", runner=runner, verification=receipt)
        self.assertEqual(report["ngspice"]["runtime_compatibility"], "unverified")
        self.assertIn("version probe", report["ngspice"]["runtime_detail"])
        self.assertNotEqual(report["flows"]["simulation"]["compatibility"], "verified")

    def test_managed_store_is_not_silently_assumed_to_be_on_the_esim_path(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "ngspice-47\n", ""))
        with patch("esim_tool_manager.health.windows_console_image", return_value=True):
            report = doctor(self.discovery({"PATH": ""}), "simulation", runner=runner)
        self.assertEqual(report["tools"]["ngspice"]["resolution"].selected.source,
                         "managed Ngspice archive")
        self.assertEqual(report["flows"]["simulation"]["status"], "unverified")
        self.assertIn("ESIM_NGSPICE", " ".join(report["flows"]["simulation"]["issues"]))


class NewCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.host = Host("windows", "10.0.22631", "amd64")
        self.root = make_root(self.base / "workspace", "eSim Installed")
        self.binary = executable(self.base / "store/ngspice/47/bin/ngspice_con.exe")
        (self.binary.parent.parent / "share/ngspice").mkdir(parents=True)
        self.enterContext(patch.object(cli.Host, "detect", return_value=self.host))
        self.enterContext(patch.object(cli.Path, "home", return_value=self.base / "home"))
        self.enterContext(patch.dict(os.environ, {
            "LOCALAPPDATA": str(self.base / "local"), "PATH": "",
            "ProgramFiles": str(self.base / "Program Files"),
            "ESIM_NGSPICE": str(self.binary),
        }, clear=True))

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                status = cli.main(args)
            except SystemExit as exc:
                status = exc.code
        return status, stdout.getvalue(), stderr.getvalue()

    def test_env_json_and_apply_are_process_scoped(self):
        with patch.object(cli, "probe", return_value=Probe(version="47", banner="ngspice-47")):
            before = dict(os.environ)
            code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "env")
            self.assertEqual(code, 0)
            self.assertEqual(os.environ, before)
            payload = json.loads(output)
            self.assertEqual(payload["simulator"], str(self.binary.resolve()))
            self.assertEqual(payload["version"], "47")
            self.assertIn("PATH", [change["variable"] for change in payload["prepared"]["changes"]])
            self.assertIsNone(payload["applied"])

            code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "env", "--apply")
            self.assertEqual(code, 0)
            applied = json.loads(output)["applied"]
            self.assertTrue(applied["verified"])
            self.assertEqual(applied["version"], "47")

    def test_env_json_does_not_disclose_inherited_secrets(self):
        fake_secret_path = r"C:\secret_token_in_path\bin"
        with patch.dict(os.environ, {"SECRET_TOKEN_XYZ": "super_secret_value_12345",
                                     "AWS_FAKE_KEY": "fake_aws_secret_key",
                                     "PATH": fake_secret_path + os.pathsep + os.environ.get("PATH", "")}), \
             patch.object(cli, "probe", return_value=Probe(version="47", banner="ngspice-47")):
            code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "env")
        self.assertEqual(code, 0)
        self.assertNotIn("super_secret_value_12345", output)
        self.assertNotIn("fake_aws_secret_key", output)
        self.assertNotIn("secret_token_in_path", output)
        payload = json.loads(output)
        env_dump = payload["prepared"]["environment"]
        self.assertNotIn("SECRET_TOKEN_XYZ", env_dump)
        self.assertNotIn("AWS_FAKE_KEY", env_dump)
        self.assertIn("ESIM_NGSPICE", env_dump)
        changes = payload["prepared"]["changes"]
        path_changes = [c for c in changes if c["variable"] == "PATH"]
        self.assertTrue(path_changes)
        self.assertEqual(path_changes[0]["previous"], "(preserved)")

    def test_verify_records_success_and_failure_receipts(self):
        receipt_path = self.base / "local/eSimToolManager/verification.json"
        with patch.object(cli, "probe", return_value=Probe(version="47", banner="ngspice-47")), \
             patch.object(cli, "analog_smoke_test", return_value=result(self.binary)):
            code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "verify", "ngspice")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["compatibility"], "verified")
        self.assertEqual(verification.load(receipt_path)["status"], "verified")

        with patch.object(cli, "probe", return_value=Probe(version="47", banner="ngspice-47")), \
             patch.object(cli, "analog_smoke_test",
                          return_value=result(self.binary, verified=False, observed=0.5,
                                              reasons=("wrong value",))):
            code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "verify", "ngspice")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["compatibility"], "unverified")
        self.assertEqual(verification.load(receipt_path)["status"], "failed")

    def test_verify_without_a_simulator_is_an_actionable_error(self):
        os.environ.pop("ESIM_NGSPICE")
        code, _, error = self.invoke("--esim-root", str(self.root), "verify", "ngspice")
        self.assertEqual(code, 2)
        self.assertIn("No ngspice executable was selected", error)

    def test_validate_runs_detection_and_the_isolated_simulation(self):
        candidate = Candidate(self.binary, "ESIM_NGSPICE", False)
        report = {"tools": {"ngspice": {"resolution": Resolution(candidate, (), ()),
                                        "probe": Probe(version="47")}},
                  "flows": {"simulation": {"status": "unverified", "compatibility": "unverified",
                                           "issues": [], "dependencies": ["ngspice"],
                                           "compatibility_detail": ""}}}
        with patch.object(cli, "doctor", return_value=report), \
             patch.object(cli, "analog_smoke_test", return_value=result(self.binary)):
            code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "validate")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["overall"], "pass")
        self.assertEqual(payload["checks"]["esim_environment"]["status"], "configured")
        self.assertEqual(payload["checks"]["simulation"]["status"], "verified")
        self.assertTrue((self.base / "local/eSimToolManager/verification.json").is_file())

    def test_validate_refuses_to_run_a_broken_managed_binary(self):
        candidate = Candidate(self.binary, "ESIM_NGSPICE", False)
        report = {"tools": {"ngspice": {"resolution": Resolution(candidate, (), ()),
                                        "probe": Probe(version="47")}},
                  "flows": {"simulation": {"status": "unverified", "compatibility": "unverified",
                                           "issues": [], "dependencies": ["ngspice"],
                                           "compatibility_detail": ""}}}
        managed = ManagedInspection(
            self.binary.parent.parent, "broken managed installation", False, None, True, False,
            "47", "47", NGSPICE.sha256, NGSPICE.sha256, 1, problems=("changed binary",),
        )
        with patch.object(cli, "doctor", return_value=report), \
             patch.object(cli, "managed_state", return_value=managed), \
             patch.object(cli, "uses_managed", return_value=True), \
             patch.object(cli, "analog_smoke_test") as smoke:
            code, output, _ = self.invoke("--json", "--esim-root", str(self.root), "validate")
        self.assertEqual(code, 1)
        smoke.assert_not_called()
        payload = json.loads(output)
        self.assertEqual(payload["overall"], "fail")
        self.assertEqual(payload["checks"]["simulation"]["status"], "blocked")
        self.assertIn("receipt or checksum", payload["checks"]["simulation"]["detail"])

    def test_json_guard_allows_safe_env_apply_but_not_config_set(self):
        with patch.object(cli, "probe", return_value=Probe(version="47", banner="ngspice-47")):
            code, _, error = self.invoke("--json", "--esim-root", str(self.root), "env", "--apply")
        self.assertEqual(code, 0)
        self.assertNotIn("read-only", error)
        code, _, error = self.invoke("--json", "config", "set", "esim_root", str(self.root))
        self.assertEqual(code, 2)
        self.assertIn("read-only", error)


@unittest.skipUnless(os.environ.get("ESIM_TOOL_MANAGER_REAL_TESTS") == "1",
                     "Set ESIM_TOOL_MANAGER_REAL_TESTS=1 for managed Ngspice integration")
class RealManagedNgspiceTests(unittest.TestCase):
    def test_verified_receipt_and_analog_simulation(self):
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        directory = local / "eSimToolManager/tools/ngspice" / NGSPICE.version
        inspection = inspect_managed(directory, NGSPICE)
        self.assertTrue(inspection.verified, inspection.problems)
        from esim_tool_manager.environment import prepare
        from esim_tool_manager.discovery import Discovery
        from esim_tool_manager.runtime import Host

        host = Host.detect()
        discovery = Discovery(host, dict(os.environ), Path.home(), None, "inherited")
        candidate = Candidate(inspection.binary, "managed Ngspice archive", False)
        plan = prepare(discovery, candidate, inspection.expected_version)
        outcome = analog_smoke_test(inspection.binary, plan.environment)
        self.assertTrue(outcome.verified, outcome.reasons)
        self.assertAlmostEqual(outcome.observed, 1.0, places=6)
