import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from esim_tool_manager.discovery import Candidate
from esim_tool_manager.packages import PackageManager, PackageOperationError, WINGET_HASH_MISMATCH, WINGET_NO_MATCH
from esim_tool_manager.runtime import Host, ManagerError


def result(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def win_candidate(version, digest="a" * 64):
    return result(stdout=f"Found KiCad [KiCad.KiCad]\nVersion: {version}\n"
                         f"Installer:\n  Installer Url: https://example.invalid/kicad-{version}.exe\n"
                         f"  Installer SHA256: {digest}\n")


def win_installed(version):
    return result(stdout=f"Name  Id           Version  Available  Source\nKiCad KiCad.KiCad  {version}    10.0.0     winget\n")


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.gettempdir()) / "mock managers"
        self.win_host = Host("windows", "10.0.26100", "amd64")
        self.ubuntu = Host("ubuntu", "24.04", "x86_64")

    def manager(self, *responses, host=None):
        runner = Mock(side_effect=responses)
        pm = PackageManager(host or self.win_host, runner=runner,
                            which=lambda name: self.base / (name + ".exe"))
        return pm, runner

    def test_winget_preview_uses_live_version_and_exact_scope_source_arch(self):
        pm, runner = self.manager(result(WINGET_NO_MATCH), win_candidate("10.0.8"))
        plan = pm.plan("install", "kicad")
        self.assertEqual((plan.package, plan.target, plan.installed, plan.latest_available),
                         ("KiCad.KiCad", "10.0.8", None, "10.0.8"))
        self.assertEqual(plan.command[1:], ("install", "--id", "KiCad.KiCad", "--exact", "--source", "winget",
                                            "--scope", "machine", "--architecture", "x64", "--version", "10.0.8",
                                            "--no-upgrade"))
        self.assertEqual([call.args[0][1] for call in runner.call_args_list], ["list", "show"])
        self.assertNotIn("--version", runner.call_args.args[0])
        self.assertEqual(plan.installer_sha256, "a" * 64)
        self.assertIn("netlister range", plan.compatibility)

    def test_explicit_version_is_fetched_exactly_and_latest_visible(self):
        pm, runner = self.manager(result(WINGET_NO_MATCH), win_candidate("10.0.8"), win_candidate("9.0.3"))
        plan = pm.plan("install", "kicad", "9.0.3")
        self.assertEqual((plan.target, plan.latest_available, plan.requested_version), ("9.0.3", "10.0.8", "9.0.3"))
        self.assertIn("--version", runner.call_args.args[0])
        self.assertEqual(runner.call_args.args[0][-2], "9.0.3")

    def test_wrong_package_or_version_in_catalog_is_rejected(self):
        for output in ("Found Other [Other.Tool]\nVersion: 9.0.3", "Found KiCad [KiCad.KiCad]\nVersion: 9.0.4"):
            pm, _ = self.manager(result(WINGET_NO_MATCH), win_candidate("10.0.8"), result(stdout=output))
            with self.assertRaisesRegex(ManagerError, "exact package and version"):
                pm.plan("install", "kicad", "9.0.3")

    def test_invalid_platform_and_unsupported_package_never_run_tools(self):
        for host, tool in ((Host("windows", "10.0.19045", "amd64"), "kicad"),
                           (Host("windows", "10.0.26100", "arm64"), "kicad"),
                           (Host("ubuntu", "22.04", "amd64"), "kicad"),
                           (self.win_host, "ngspice")):
            with self.subTest(host=host, tool=tool):
                pm, runner = self.manager(host=host)
                with self.assertRaises(ManagerError):
                    pm.plan("install", tool)
                runner.assert_not_called()

    def test_missing_manager_returns_actionable_error(self):
        pm = PackageManager(self.win_host, which=lambda _: None)
        with self.assertRaisesRegex(ManagerError, "winget is unavailable"):
            pm.plan("install", "kicad")

    def test_windows_updates_respect_winget_eligibility(self):
        pm, _ = self.manager(win_installed("9.0.3"), result(WINGET_NO_MATCH))
        blocked = pm.plan("upgrade", "kicad")
        self.assertEqual(blocked.status, "no eligible WinGet upgrade")
        self.assertEqual(blocked.command, ())
        pm, _ = self.manager(win_installed("9.0.3"), win_installed("9.0.3"), win_candidate("10.0.0"))
        plan = pm.plan("upgrade", "kicad")
        self.assertEqual(plan.target, "10.0.0")
        self.assertEqual(plan.command[1], "upgrade")

    def test_upgrade_rejects_incompatible_major(self):
        pm, _ = self.manager(win_installed("9.0.3"), win_installed("9.0.3"), win_candidate("11.0.0"))
        with self.assertRaisesRegex(ManagerError, "major range"):
            pm.plan("upgrade", "kicad")

    def test_apt_uses_dpkg_comparison_and_exact_candidate(self):
        pm, runner = self.manager(result(stdout="installed\t1:9.0.3+dfsg-1\n"),
                                  result(stdout="kicad:\n  Installed: 1:9.0.3+dfsg-1\n  Candidate: 1:9.0.8+dfsg-1\n"),
                                  result(), host=self.ubuntu)
        plan = pm.plan("upgrade", "kicad")
        self.assertEqual(plan.command[-4:], ("--no-remove", "--only-upgrade", "install", "kicad=1:9.0.8+dfsg-1"))
        self.assertEqual(runner.call_args.args[0][1:], ("--compare-versions", "1:9.0.3+dfsg-1", "lt", "1:9.0.8+dfsg-1"))
        self.assertEqual(runner.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_apt_exact_version_requires_metadata(self):
        none = result(1, stderr="dpkg-query: no packages found matching kicad")
        for output in ("Package: other\nVersion: 9.0.8+dfsg-1", "Package: kicad\nVersion: 9.0.7+dfsg-1"):
            pm, _ = self.manager(none, result(stdout=output), host=self.ubuntu)
            with self.assertRaisesRegex(ManagerError, "No matching APT version"):
                pm.plan("install", "kicad", "9.0.8+dfsg-1")

    def test_stale_plan_rejected_before_mutation(self):
        pm, runner = self.manager(result(WINGET_NO_MATCH), win_candidate("9.0.3"), win_installed("9.0.3"))
        plan = pm.plan("install", "kicad")
        with self.assertRaisesRegex(ManagerError, "state changed"):
            pm.execute(plan, Mock())
        self.assertTrue(all(not call.kwargs.get("interactive") for call in runner.call_args_list))

    def test_same_version_with_changed_installer_hash_rejected_after_approval(self):
        absent = result(WINGET_NO_MATCH)
        pm, runner = self.manager(absent, win_candidate("9.0.3"), absent, win_candidate("9.0.3", "b" * 64))
        plan = pm.plan("install", "kicad")
        with self.assertRaisesRegex(ManagerError, "Review a new plan"):
            pm.execute(plan, Mock())
        self.assertEqual([call.args[0][1] for call in runner.call_args_list], ["list", "show", "list", "show"])

    def test_changed_latest_candidate_needs_new_approval(self):
        absent = result(WINGET_NO_MATCH)
        pm, runner = self.manager(absent, win_candidate("10.0.8"), absent, win_candidate("10.0.9"))
        plan = pm.plan("install", "kicad")
        with self.assertRaisesRegex(ManagerError, "Review a new plan"):
            pm.execute(plan, Mock())
        self.assertTrue(all(not call.kwargs.get("interactive") for call in runner.call_args_list))

    def test_newer_latest_does_not_supersede_explicit_version(self):
        absent = result(WINGET_NO_MATCH)
        pinned = win_candidate("10.0.8")
        pm, runner = self.manager(absent, win_candidate("10.0.9"), pinned, absent, win_candidate("10.0.10"), pinned,
                                  result(0x80072EE2), absent)
        with self.assertRaises(PackageOperationError) as error:
            pm.execute(pm.plan("install", "kicad", "10.0.8"), Mock())
        self.assertEqual(error.exception.category, "download_failure")
        self.assertEqual(error.exception.state, "unchanged")
        mutation = next(call.args[0] for call in runner.call_args_list if call.kwargs.get("interactive"))
        self.assertEqual(mutation[-2:], ("10.0.8", "--no-upgrade"))

    def test_winget_refuses_unidentified_installer(self):
        pm, runner = self.manager(result(WINGET_NO_MATCH), result(stdout="Found KiCad [KiCad.KiCad]\nVersion: 9.0.3\n"))
        with self.assertRaisesRegex(ManagerError, "URL and SHA-256"):
            pm.plan("install", "kicad")
        self.assertEqual(runner.call_count, 2)

    def test_success_is_verified_with_exact_system_executable(self):
        absent = result(WINGET_NO_MATCH)
        pm, runner = self.manager(absent, win_candidate("9.0.3"), absent, win_candidate("9.0.3"), result(),
                                  win_installed("9.0.3"), result(stdout="9.0.3\n"))
        path = self.base / "Program Files/KiCad/9.0/bin/kicad-cli.exe"
        candidate = Candidate(path, "Program Files KiCad", bundled=False)
        discovery = SimpleNamespace(system_tools=lambda _: [candidate])
        verified = pm.execute(pm.plan("install", "kicad"), discovery)
        self.assertEqual((verified["executable"], verified["executable_version"]), (path, "9.0.3"))
        self.assertEqual(sum(bool(call.kwargs.get("interactive")) for call in runner.call_args_list), 1)

    def test_apt_installer_checks_binary_ownership_and_version(self):
        absent = result(1, stderr="dpkg-query: no packages found matching kicad")
        selected = "9.0.8+dfsg-1"
        pm, _ = self.manager(absent, result(stdout=f"  Candidate: {selected}\n"), absent,
                             result(stdout=f"  Candidate: {selected}\n"), result(),
                             result(stdout=f"installed\t{selected}\n"), result(stdout="kicad: /usr/bin/kicad-cli\n"),
                             result(stdout="9.0.8\n"), host=self.ubuntu)
        path = Path("/usr/bin/kicad-cli")
        discovery = SimpleNamespace(system_tools=lambda _: [Candidate(path, "system package directory", False)])
        verified = pm.execute(pm.plan("install", "kicad"), discovery)
        self.assertEqual(verified["package_version"], selected)
        self.assertEqual(verified["executable_version"], "9.0.8")

    def test_package_success_without_matching_binary_is_error(self):
        absent = result(WINGET_NO_MATCH)
        pm, _ = self.manager(absent, win_candidate("9.0.3"), absent, win_candidate("9.0.3"), result(),
                             win_installed("9.0.3"))
        with self.assertRaisesRegex(ManagerError, "executable version could not be verified"):
            pm.execute(pm.plan("install", "kicad"), SimpleNamespace(system_tools=lambda _: []))

    def test_failed_or_timed_out_mutation_rechecks_package(self):
        absent = result(WINGET_NO_MATCH)
        for failure, label in ((result(67), "exit 67"), (ManagerError("timed out"), "timed out")):
            pm, _ = self.manager(absent, win_candidate("9.0.3"), absent, win_candidate("9.0.3"), failure,
                                 win_installed("9.0.3"))
            with self.assertRaisesRegex(ManagerError, rf"{label}.*after the attempt: 9\.0\.3"):
                pm.execute(pm.plan("install", "kicad"), Mock())

    def test_hash_mismatch_preserves_hresult_and_observed_unchanged_state(self):
        absent = result(WINGET_NO_MATCH)
        for code in (WINGET_HASH_MISMATCH, WINGET_HASH_MISMATCH - 2**32):
            with self.subTest(code=code):
                pm, runner = self.manager(absent, win_candidate("10.0.8"), absent, win_candidate("10.0.8"),
                                          result(code, stderr="Installer hash does not match"), absent)
                with self.assertRaises(PackageOperationError) as error:
                    pm.execute(pm.plan("install", "kicad"), Mock())
                failure = error.exception
                self.assertEqual((failure.category, failure.returncode, failure.hresult),
                                 ("integrity_verification_failure", code, "0x8A150011"))
                self.assertEqual((failure.observed, failure.state), ("absent", "unchanged"))
                self.assertIn("winget source update", str(failure))
                self.assertEqual(failure.native_output, "Installer hash does not match")
                self.assertEqual(sum(bool(call.kwargs.get("interactive")) for call in runner.call_args_list), 1)
