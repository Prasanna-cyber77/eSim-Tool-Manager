import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from esim_tool_manager.discovery import Discovery
from esim_tool_manager.health import doctor, probe
from esim_tool_manager.runtime import Host, ManagerError, run


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "eSim tools"
        self.home = self.base / "home"
        self.system = self.base / "Program Files"
        self.path_bin = self.base / "inherited"
        self.env = {"ProgramFiles": str(self.system), "PATH": str(self.path_bin)}
        self.host = Host("windows", "10.0.22631", "amd64")

    def file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".exe":
            self.write_pe(path, 3)
        else:
            path.touch()
        path.chmod(0o755)
        return path

    def write_pe(self, path, subsystem):
        image = bytearray(158)
        image[:2] = b"MZ"
        image[60:64] = (64).to_bytes(4, "little")
        image[64:68] = b"PE\0\0"
        image[84:86] = (70).to_bytes(2, "little")
        image[88:90] = b"\x0b\x02"
        image[156:158] = subsystem.to_bytes(2, "little")
        path.write_bytes(image)

    def discovery(self, launcher="python"):
        return Discovery(self.host, self.env, self.home, self.root, launcher)

    def ini(self, data):
        self.file(self.home / ".nghdl/config.ini").write_text(data, encoding="utf-8")

    def test_bundled_and_system_kicad_both_reported(self):
        kicad = self.file(self.root / "tools/kicad/bin/kicad-cli.exe")
        self.file(kicad.with_name("eeschema.exe"))
        system = self.file(self.system / "KiCad/9.0/bin/kicad-cli.exe")
        self.file(system.with_name("eeschema.exe"))
        discovery = self.discovery()
        result = discovery.resolve("kicad")
        self.assertEqual(result.selected.path, kicad)
        self.assertTrue(result.selected.bundled)
        self.assertEqual(result.alternatives[0].path, system)
        self.assertFalse(result.alternatives[0].bundled)
        self.assertEqual(discovery.system_tools("kicad")[0].path, system)

    def test_system_kicad_is_rediscovered_after_package_change(self):
        discovery = self.discovery()
        self.assertEqual(discovery.system_tools("kicad"), [])
        system = self.file(self.system / "KiCad/9.0/bin/kicad-cli.exe")
        self.file(system.with_name("eeschema.exe"))
        self.assertEqual(discovery.system_tools("kicad")[0].path, system)

    def test_unchanged_environment_and_three_launcher_orders(self):
        custom = self.file(self.root / "tools/nghdl/install_dir/bin/ngspice.exe")
        self.file(self.root / "tools/nghdl/install_dir/lib/ngspice/ivlng.dll")
        stock = self.file(self.root / "tools/ngspice/bin/ngspice.exe")
        system = self.file(self.path_bin / "ngspice.exe")
        lib = self.root / "tools/nghdl/install_dir/lib/ngspice"
        for variant, expected in {"python": [lib, custom.parent, stock.parent],
                                  "batch": [custom.parent, lib, stock.parent],
                                  "native": [custom.parent, stock.parent]}.items():
            with self.subTest(variant=variant):
                discovery = self.discovery(variant)
                self.assertEqual([path for path, _ in discovery.path_dirs], expected + [system.parent])
                self.assertEqual(discovery.resolve("ngspice").selected.path, custom)
                self.assertEqual(discovery.env["SPICE_LIB_DIR"], str(custom.parent.parent / "share/ngspice"))
        self.assertNotIn("SPICE_LIB_DIR", self.env)

    def test_sentinel_skips_python_launcher_environment(self):
        self.env["ESIM_ENV_READY"] = "1"
        self.file(self.root / "tools/nghdl/install_dir/bin/ngspice.exe")
        system = self.file(self.path_bin / "ngspice.exe")
        self.assertEqual(self.discovery().resolve("ngspice").selected.path, system)

    def test_ngspice_environment_then_ini_then_path(self):
        stock = self.file(self.path_bin / "ngspice.exe")
        configured = self.file(self.base / "custom/install_dir/bin/ngspice.exe")
        self.ini(f"[NGHDL]\nNGHDL_HOME = {configured.parents[2]}\nRELEASE = %(NGHDL_HOME)s/release\n")
        override = self.file(self.base / "another/ngspice.exe")
        self.env["ESIM_NGSPICE"] = str(override)
        self.assertEqual(self.discovery().resolve("ngspice").selected.path, override)
        override.unlink()
        discovery = self.discovery()
        result = discovery.resolve("ngspice")
        self.assertEqual(result.selected.path, configured)
        self.assertEqual(result.alternatives[0].path, stock)
        self.assertIn("Ignoring invalid", result.warnings[0])
        self.assertEqual(Path(discovery.cfg("NGHDL", "RELEASE")), configured.parents[2] / "release")

    def test_invalid_kicad_override_blocks_fallback(self):
        self.file(self.path_bin / "kicad-cli.exe")
        self.env["ESIM_KICAD_CLI"] = str(self.base / "absent.exe")
        result = self.discovery().resolve("kicad")
        self.assertIsNone(result.selected)
        self.assertIn("ESIM_KICAD_CLI", result.warnings[0])

    def test_vvp_prefers_selected_compiler_sibling(self):
        compiler = self.file(self.base / "custom/bin/iverilog.exe")
        sibling = self.file(compiler.with_name("vvp.exe"))
        self.file(self.root / "library/bin/iverilog/bin/vvp.exe")
        self.env["ESIM_IVERILOG"] = str(compiler)
        self.assertEqual(self.discovery().resolve("vvp").selected.path, sibling)

    def test_stock_shim_and_cosim_artifacts_with_identical_banners(self):
        def mock_runner(argv, **kwargs):
            names = {"ngspice.exe": "*** ngspice-45.2 : Circuit level simulation program",
                     "iverilog.exe": "Icarus Verilog version 12.0",
                     "vvp.exe": "Icarus Verilog runtime version 12.0"}
            return subprocess.CompletedProcess(argv, 0, "\n", names[argv[0].name])

        prefix = self.root / "tools/nghdl/install_dir"
        self.file(prefix / "bin/ngspice.exe")
        self.file(prefix / "lib/ngspice/digital.cm")
        self.file(self.root / "library/bin/iverilog/bin/iverilog.exe")
        self.file(self.root / "library/bin/iverilog/bin/vvp.exe")
        stock = doctor(self.discovery(), runner=mock_runner)
        self.assertEqual(stock["flows"]["simulation"]["status"], "ready")
        self.assertEqual(stock["flows"]["verifier"]["status"], "ready")
        self.assertEqual(stock["flows"]["dcosim"]["status"], "missing")
        self.assertIn("standard", stock["ngspice"]["flavor"])
        for name in ("ivlng.dll", "ivlng.vpi", "ghdl.cm"):
            self.file(prefix / "lib/ngspice" / name)
        self.file(self.root / "library/bin/iverilog/bin/libvvp.dll")
        custom = doctor(self.discovery(), runner=mock_runner)
        self.assertEqual(custom["flows"]["dcosim"]["status"], "ready")
        self.assertEqual(custom["flows"]["dcosim"]["compatibility"], "unverified")
        self.assertEqual(custom["flows"]["nghdl"]["status"], "missing")
        self.assertIn("eSim co-simulation", custom["ngspice"]["flavor"])
        self.assertEqual(stock["tools"]["ngspice"]["probe"].version, custom["tools"]["ngspice"]["probe"].version)

    def test_ubuntu_mcode_detected_in_full_banner(self):
        self.host = Host("ubuntu", "24.04", "x86_64")
        self.file(self.path_bin / "ngspice")
        self.file(self.path_bin / "ghdl")

        def mock_runner(argv, **kwargs):
            output = "GHDL 4.1.0\n mcode JIT code generator" if argv[0].name == "ghdl" else "ngspice-45.2"
            return subprocess.CompletedProcess(argv, 0, output, "")

        discovery = self.discovery()
        # The fixture's Windows drive letter contains the POSIX PATH separator.
        discovery.path_dirs = [(self.path_bin, "inherited PATH")]
        # These are POSIX fixtures even when the test runner runs on Windows.
        with patch("esim_tool_manager.health.windows_console_image", return_value=True):
            report = doctor(discovery, "nghdl", runner=mock_runner)
        self.assertEqual(report["launcher"], "inherited")
        self.assertEqual(report["flows"]["nghdl"]["status"], "incompatible")
        self.assertIn("mcode", " ".join(report["flows"]["nghdl"]["issues"]))

    def test_nonzero_exit_does_not_become_a_valid_banner(self):
        kicad = self.file(self.path_bin / "kicad-cli.exe")
        candidate = self.discovery().resolve("kicad").selected
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([kicad], 1, "9.0.3", "")):
            result = probe("kicad", candidate, None)
        self.assertIsNone(result.version)
        self.assertIn("exited with 1", result.error)

    def test_focused_doctor_reports_unsupported_and_supported_kicad_versions(self):
        cli = self.file(self.path_bin / "kicad-cli.exe")
        self.file(self.path_bin / "ngspice.exe")
        for banner, expected in (("6.0.9", "incompatible"), ("9.0.3", "verified"), ("11.0.0", "unverified"),
                                 ("KiCad CLI version 9.0.3-rc1", "unverified")):
            with self.subTest(banner=banner):
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, banner, ""))
                report = doctor(self.discovery(), "netlisting", runner=runner)
                runner.assert_called_once_with([cli, "version"], env=self.discovery().child_env(), timeout=10,
                                               interactive=False)
                self.assertEqual(report["flows"]["netlisting"]["compatibility"], expected)

    def test_version_probe_parses_ansi_bom_stderr_and_labels(self):
        examples = {"kicad": ("kicad-cli.exe", "\ufeff\x1b[36mKiCad CLI Version: v9.0.3+vendor\x1b[0m", "9.0.3"),
                    "ngspice": ("ngspice.exe", "** ngspice version: 45.2 (XSPICE)", "45.2"),
                    "vvp": ("vvp.exe", "Icarus Verilog runtime version 12.0 (stable)", "12.0")}
        for tool, (filename, banner, version) in examples.items():
            with self.subTest(tool=tool):
                self.file(self.path_bin / filename)
                selected = self.discovery().resolve(tool).selected
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, "Build information\n", banner))
                result = probe(tool, selected, {}, runner)
                self.assertEqual(result.version, version)
                self.assertNotIn("\x1b", result.banner)

    def test_presence_checks_and_known_gui_overrides_never_spawn(self):
        editor = self.file(self.path_bin / "eeschema.exe")
        self.env["ESIM_KICAD_CLI"] = str(editor)
        gui = self.file(self.path_bin / "ngspice_gui.exe")
        self.env["ESIM_NGSPICE_GUI"] = str(gui)
        discovery = self.discovery()
        runner = Mock()
        for tool in ("eeschema", "ngspice-gui"):
            self.assertIsNone(probe(tool, discovery.resolve(tool).selected, {}, runner).version)
        self.assertIn("graphical", probe("kicad", discovery.resolve("kicad").selected, {}, runner).error)
        runner.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows PE launch guard")
    def test_gui_subsystem_ngspice_rejected_without_launch(self):
        path = self.file(self.path_bin / "ngspice.exe")
        self.write_pe(path, 2)
        runner = Mock()
        result = probe("ngspice", self.discovery().resolve("ngspice").selected, {}, runner)
        self.assertIn("not a console executable", result.error)
        runner.assert_not_called()

    def test_probe_timeout_is_an_unverified_workflow(self):
        self.file(self.path_bin / "kicad-cli.exe")
        runner = Mock(side_effect=ManagerError("version probe timed out"))
        report = doctor(self.discovery(), "netlisting", runner=runner)
        flow = report["flows"]["netlisting"]
        self.assertEqual((flow["status"], flow["compatibility"]), ("unverified", "unverified"))
        self.assertIn("timed out", " ".join(flow["issues"]))

    def test_native_runner_timeout_and_argument_boundaries(self):
        output = run([sys.executable, "-c", "import sys; print(sys.argv[1])", "é & a path"],
                     env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(output.stdout.strip(), "é & a path")
        with self.assertRaisesRegex(ManagerError, "timed out"):
            run([sys.executable, "-c", "import time; time.sleep(3)"], timeout=0.1)
