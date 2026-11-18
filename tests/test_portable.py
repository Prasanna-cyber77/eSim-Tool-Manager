import io
import os
import shutil
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import py7zr

from esim_tool_manager import portable as portable_module
from esim_tool_manager.discovery import Discovery
from esim_tool_manager.health import Probe, doctor, inventory
from esim_tool_manager.portable import NGSPICE, PortableManager, download, normalize_7z, safe_extract, sha256
from esim_tool_manager.runtime import Host, ManagerError, run


class PortableTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        system = self.base / "Windows"
        (system / "System32").mkdir(parents=True)
        (system / "System32/tar.exe").touch()
        self.host = Host("windows", "10.0.22631", "amd64")
        self.env = {"SystemRoot": str(system)}
        self.store = self.base / "managed tools"
        self.payload = self.base / "fixture.tar"
        self.fixture()
        self.release = replace(NGSPICE, sha256=sha256(self.payload))
        self.fetch = Mock(side_effect=lambda _url, target: shutil.copyfile(self.payload, target))
        self.convert = Mock(side_effect=lambda source, target, _tar: shutil.copyfile(source, target))
        self.version = Mock(return_value="47")
        self.manager = PortableManager(self.host, self.store, self.env, release=self.release,
                                       fetch=self.fetch, convert=self.convert)

    def fixture(self, extra=None):
        with tarfile.open(self.payload, "w") as archive:
            binary = tarfile.TarInfo("Spice64/bin/ngspice_con.exe")
            body = b"test console executable fixture"
            binary.size = len(body)
            archive.addfile(binary, io.BytesIO(body))
            support = tarfile.TarInfo("Spice64/share/ngspice/spinit")
            support.size = 4
            archive.addfile(support, io.BytesIO(b"init"))
            if extra:
                archive.addfile(extra)

    def assert_no_staged_install(self, plan):
        self.assertFalse(plan.destination.exists())
        self.assertEqual(list(plan.destination.parent.glob(".staging-*")), [])

    def test_preview_then_atomic_install_and_idempotent_plan(self):
        plan = self.manager.plan()
        self.assertEqual(plan.status, "available")
        self.assertFalse(self.store.exists())
        self.fetch.assert_not_called()
        binary, version = self.manager.execute(plan, self.version)
        self.assertEqual((binary, version), (plan.destination / "bin/ngspice_con.exe", "47"))
        self.assertTrue(binary.is_file())
        self.assertTrue((plan.destination / "share/ngspice/spinit").is_file())
        self.assertEqual(self.manager.plan().status, "already installed")
        self.assertEqual(list(plan.destination.parent.glob(".staging-*")), [])
        with self.assertRaisesRegex(ManagerError, "state changed"):
            self.manager.execute(plan, self.version)
        self.fetch.assert_called_once()

    def test_untrusted_or_missing_hash_blocks_before_fetch(self):
        self.manager.release = replace(self.release, sha256=None)
        plan = self.manager.plan()
        self.assertEqual(plan.status, "verification blocked")
        with self.assertRaises(ManagerError):
            self.manager.execute(plan, self.version)
        self.fetch.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_invalid_checksum_prevents_extraction_then_can_retry(self):
        plan = self.manager.plan()
        self.fetch.side_effect = lambda _url, target: target.write_bytes(b"truncated or altered archive")
        with self.assertRaisesRegex(ManagerError, "Expected .* got"):
            self.manager.execute(plan, self.version)
        self.assert_no_staged_install(plan)
        self.convert.assert_not_called()
        self.version.assert_not_called()
        self.fetch.side_effect = lambda _url, target: shutil.copyfile(self.payload, target)
        self.assertTrue(self.manager.execute(self.manager.plan(), self.version)[0].exists())

    def test_corrupt_archive_and_version_mismatch_never_publish(self):
        plan = self.manager.plan()
        self.convert.side_effect = lambda _source, target, _tar: target.write_bytes(b"not a tar archive")
        with self.assertRaisesRegex(ManagerError, "corrupt"):
            self.manager.execute(plan, self.version)
        self.assert_no_staged_install(plan)
        self.convert.side_effect = lambda source, target, _tar: shutil.copyfile(source, target)
        self.version.return_value = "46"
        with self.assertRaisesRegex(ManagerError, "approved version"):
            self.manager.execute(plan, self.version)
        self.assert_no_staged_install(plan)

    def test_rejects_traversal_symlinks_hardlinks_and_windows_devices(self):
        for name, kind in (("Spice64/../escape", tarfile.REGTYPE), ("Spice64/bin/link", tarfile.SYMTYPE),
                           ("Spice64/bin/hard", tarfile.LNKTYPE), ("Spice64/bin/CON.txt", tarfile.REGTYPE),
                           ("Spice64/bin/program.exe:stream", tarfile.REGTYPE)):
            with self.subTest(name=name):
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = "../../outside"
                self.fixture(entry)
                self.manager.release = replace(self.release, sha256=sha256(self.payload))
                plan = self.manager.plan()
                with self.assertRaisesRegex(ManagerError, "Unsafe archive entry"):
                    self.manager.execute(plan, self.version)
                self.assert_no_staged_install(plan)
        self.version.assert_not_called()

    def test_interruption_and_failed_download_leave_no_install(self):
        plan = self.manager.plan()

        def interrupt(_url, target):
            target.write_bytes(b"partial bytes")
            raise KeyboardInterrupt

        self.fetch.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.manager.execute(plan, self.version)
        self.assert_no_staged_install(plan)
        self.fetch.side_effect = ManagerError("connection closed")
        with self.assertRaisesRegex(ManagerError, "connection closed"):
            self.manager.execute(plan, self.version)
        self.assert_no_staged_install(plan)

    def test_modified_installed_support_file_is_reported_as_broken(self):
        plan = self.manager.plan()
        self.manager.execute(plan, self.version)
        (plan.destination / "share/ngspice/spinit").write_bytes(b"changed")
        broken = self.manager.plan()
        self.assertEqual(broken.status, "broken managed installation")
        with self.assertRaises(ManagerError):
            self.manager.execute(broken, self.version)
        self.fetch.assert_called_once()

    def test_missing_archive_version_and_missing_decoder(self):
        with self.assertRaisesRegex(ManagerError, "No reviewed portable Ngspice archive"):
            self.manager.plan("46")
        with patch("esim_tool_manager.portable.importlib.util.find_spec", return_value=None):
            self.assertEqual(self.manager.plan().status, "project dependency py7zr unavailable")
        self.fetch.assert_not_called()

    def test_portable_install_refuses_an_esim_root_as_its_store(self):
        app = self.store / "src/frontEnd/Application.py"
        app.parent.mkdir(parents=True)
        app.touch()
        (self.store / "VERSION").touch()
        plan = self.manager.plan()
        with self.assertRaisesRegex(ManagerError, "inside an eSim installation or checkout"):
            self.manager.execute(plan, self.version)
        self.fetch.assert_not_called()
        self.assertFalse(plan.destination.parent.exists())

    def test_managed_console_is_inventory_fallback_and_preserves_esim_precedence(self):
        binary, _ = self.manager.execute(self.manager.plan(), self.version)
        with patch("esim_tool_manager.discovery.NGSPICE", self.release):
            discovery = Discovery(self.host, self.env, self.base, portable_store=self.store)
            self.assertEqual(discovery.resolve("ngspice").selected.path, binary)
            override = self.base / "ngspice.exe"
            override.touch()
            env = self.env | {"ESIM_NGSPICE": str(override)}
            discovery = Discovery(self.host, env, self.base, portable_store=self.store)
            self.assertEqual(discovery.resolve("ngspice").selected.path, override)
            self.assertEqual(discovery.resolve("ngspice").alternatives[0].path, binary)

    def test_managed_version_is_detected_while_esim_search_path_needs_configuration(self):
        binary, _ = self.manager.execute(self.manager.plan(), self.version)
        with patch("esim_tool_manager.discovery.NGSPICE", self.release), patch(
            "esim_tool_manager.health.probe", return_value=Probe(version="47")
        ):
            discovery = Discovery(self.host, self.env, self.base, portable_store=self.store)
            row = inventory(discovery, ("ngspice",))["ngspice"]
            self.assertEqual(row["status"], "detected")
            self.assertEqual(row["recommendation"], "")
            report = doctor(discovery, "simulation")
            self.assertEqual(report["tools"]["ngspice"]["status"], "detected")
            self.assertEqual(report["flows"]["simulation"]["status"], "unverified")
            self.assertIn("ESIM_NGSPICE", " ".join(report["flows"]["simulation"]["issues"]))
            env = self.env | {"ESIM_NGSPICE": str(binary)}
            configured = doctor(Discovery(self.host, env, self.base, portable_store=self.store), "simulation")
            self.assertEqual(configured["flows"]["simulation"]["status"], "ready")

    def test_download_refuses_unapproved_host_before_network(self):
        with self.assertRaisesRegex(ManagerError, "approved upstream HTTPS"):
            download("https://untrusted.invalid/fixture.7z", self.base / "untrusted.7z")

    def test_link_detection_does_not_require_windows_only_attributes(self):
        regular = self.base / "regular file"
        regular.write_bytes(b"fixture")

        class PosixStat:
            @staticmethod
            def S_ISLNK(_mode):
                return False

        with patch("esim_tool_manager.portable.stat", PosixStat):
            self.assertFalse(portable_module.is_link(regular))

    def test_solid_7z_normalization_does_not_invoke_windows_tar(self):
        source = self.base / "data"
        source.mkdir()
        first = source / "console.exe"
        second = source / "notes.txt"
        first.write_bytes(b"MZ fixture binary" * 1024)
        second.write_bytes(b"unicode and spaces fixture")
        payload = self.base / "solid.7z"
        with py7zr.SevenZipFile(payload, "w") as archive:
            archive.write(first, "Spice64/bin/ngspice_con.exe")
            archive.write(second, "Spice64/docs/notes with spaces.txt")
        intermediate = self.base / "solid.tar"
        with patch("subprocess.run", side_effect=AssertionError("Windows tar must not run")):
            normalize_7z(payload, intermediate, None)
        target = self.base / "decoded"
        safe_extract(intermediate, target, "Spice64")
        self.assertEqual((target / "Spice64/bin/ngspice_con.exe").read_bytes(), first.read_bytes())
        self.assertEqual((target / "Spice64/docs/notes with spaces.txt").read_bytes(), second.read_bytes())

    def test_corrupt_7z_decoder_returns_a_managed_error(self):
        payload = self.base / "corrupt.7z"
        payload.write_bytes(b"not a 7z archive")
        with self.assertRaisesRegex(ManagerError, "could not be decoded"):
            normalize_7z(payload, self.base / "corrupt.tar", None)

    def test_7z_metadata_with_unsafe_path_rejected_before_decoding(self):
        from types import SimpleNamespace

        archive = Mock()
        archive.__enter__ = Mock(return_value=archive)
        archive.__exit__ = Mock(return_value=False)
        archive.needs_password.return_value = False
        archive.list.return_value = [SimpleNamespace(filename="Spice64/../escape", uncompressed=1,
                                                    is_file=True, is_directory=False, is_symlink=False)]
        with patch("py7zr.SevenZipFile", return_value=archive):
            with self.assertRaisesRegex(ManagerError, "Unsafe archive entry"):
                normalize_7z(self.payload, self.base / "unsafe.tar", None)
        archive.extractall.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "uses Windows built-in libarchive")
    def test_local_7z_fixture_with_real_windows_tar(self):
        tar_exe = Path(os.environ["SystemRoot"]) / "System32/tar.exe"
        if not tar_exe.is_file():
            self.skipTest("Windows tar is unavailable")
        source = self.base / "Spice64"
        source.mkdir()
        (source / "proof.txt").write_text("local fixture", encoding="utf-8")
        payload = self.base / "fixture.7z"
        result = run([tar_exe, "-acf", payload, "-C", self.base, "Spice64"])
        self.assertEqual(result.returncode, 0, result.stderr)
        normalized = self.base / "normalized.tar"
        normalize_7z(payload, normalized, tar_exe)
        target = self.base / "extracted"
        safe_extract(normalized, target, "Spice64")
        self.assertEqual((target / "Spice64/proof.txt").read_text(encoding="utf-8"), "local fixture")
