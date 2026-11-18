"""Exact, preview-first package operations for system KiCad."""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .health import probe
from .runtime import ManagerError, run


PACKAGE_IDS = {"windows": "KiCad.KiCad", "ubuntu": "kicad"}
WINGET_NO_MATCH = 0x8A150014
WINGET_HASH_MISMATCH = 0x8A150011
INTEGRITY_FAILURE = "integrity_verification_failure"
WININET_DOWNLOAD_ERRORS = {0x80072EE2, 0x80072EFE, 0x80072F83}


@dataclass(frozen=True)
class Plan:
    action: str
    tool: str
    package: str
    installed: str | None
    target: str | None
    source: str
    command: tuple[str, ...]
    status: str
    installer_url: str | None = None
    installer_sha256: str | None = None
    latest_available: str | None = field(default=None, compare=False)
    requested_version: str | None = None
    compatibility: str = "not assessed"
    verification: str = "not assessed"


class PackageOperationError(ManagerError):
    def __init__(self, plan, observed, *, windows, result=None, cause=None):
        self.returncode = result.returncode if result is not None else None
        self.native_output = "\n".join(stream.strip() for stream in (result.stdout, result.stderr) if stream) if result is not None else ""
        self.observed = observed
        self.state = "unknown" if observed == "unknown" else (
            "unchanged" if observed == (plan.installed or "absent") else "changed")
        self.category = "package_manager_failure"
        code = self.returncode & 0xFFFFFFFF if self.returncode is not None else None
        self.hresult = f"0x{code:08X}" if windows and code is not None else None
        if windows and code == WINGET_HASH_MISMATCH:
            self.category = INTEGRITY_FAILURE
            message = ("Installer integrity verification failed. The downloaded file did not match the SHA-256 in the WinGet manifest, so WinGet stopped before running it. "
                       "Review the download evidence with `diagnose kicad` before retrying. "
                       "If the manifest is stale, refresh it with `winget source update --name winget`.")
        elif (windows and code in WININET_DOWNLOAD_ERRORS) or re.search(
            r"download failed|failed to (?:download|fetch)", self.native_output, re.I
        ):
            self.category = "download_failure"
            message = "The package manager could not download the installer. Check the connection and review its logs before retrying."
        else:
            message = str(cause) if cause is not None else f"{plan.action.capitalize()} of {plan.package} failed."
        if self.returncode is not None:
            native_code = f"{self.returncode} ({self.hresult})" if self.hresult else str(self.returncode)
            message += f" Package manager exit {native_code}."
        message += f" Observed package version after the attempt: {observed}. Installation state: {self.state}."
        self.log_hint = "Run `winget --info` for the WinGet diagnostic log directory." if windows else None
        if self.log_hint:
            message += f" {self.log_hint}"
        super().__init__(message)


def require_success(result, description):
    if result.returncode:
        output = (result.stderr or result.stdout or "").strip()
        hint = f". {output[-800:]}" if output else ""
        raise ManagerError(f"{description} failed (exit {result.returncode}){hint}")
    return result.stdout or ""


def numeric_version(value):
    if re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        parts = tuple(int(part) for part in value.split("."))
        return parts + (0,) * (4 - len(parts))
    raise ManagerError(f"Cannot safely compare KiCad version {value!r}")


def upstream_version(version):
    match = re.match(r"^(?:\d+:)?(\d+(?:\.\d+){1,3})(?![\d.])", version)
    if match is None:
        raise ManagerError(f"Unrecognized KiCad version {version!r}")
    return match.group(1)


class PackageManager:
    def __init__(self, host, runner=run, which=shutil.which):
        self.host = host
        self.runner = runner
        self.which = which
        self.windows = host.os == "windows"
        self.query_env = {**os.environ, "LC_ALL": "C"} if not self.windows else None

    def _command(self, name, *args):
        path = self.which(name)
        if not path or not Path(path).is_absolute() or (self.windows and Path(path).suffix.lower() != ".exe"):
            raise ManagerError(f"{name} is unavailable. Install or enable the platform package manager first.")
        return (str(path), *args)

    def _query(self, name, *args):
        return self.runner(self._command(name, *args), env=self.query_env, timeout=30)

    def _win_args(self, package):
        return ("--id", package, "--exact", "--source", "winget", "--scope", "machine")

    def _check_platform(self):
        if self.windows and self.host.architecture in {"amd64", "x86_64"}:
            match = re.match(r"10\.0\.(\d+)", self.host.release)
            if match and int(match.group(1)) >= 22000:
                return
        if self.host.os == "ubuntu" and self.host.release in {"24.04", "26.04"}:
            return
        raise ManagerError("Package operations support Windows 11 x64 and Ubuntu 24.04/26.04.")

    def installed(self, package, upgrade_available=False):
        if self.windows:
            options = ("--upgrade-available",) if upgrade_available else ()
            result = self._query("winget", "list", *self._win_args(package), *options, "--disable-interactivity")
            if result.returncode & 0xFFFFFFFF == WINGET_NO_MATCH:
                return None
            output = require_success(result, "WinGet inventory")
            versions = re.findall(rf"(?:^|\s){re.escape(package)}[ \t]+(\S+)", output, re.MULTILINE)
            if len(versions) == 1:
                return versions[0]
            raise ManagerError("WinGet returned ambiguous package inventory. Check its exact package row and scope.")
        result = self._query("dpkg-query", "-W", "-f=${db:Status-Status}\t${Version}\n", package)
        if result.returncode == 1 and "no packages found matching" in (result.stderr or ""):
            return None
        output = require_success(result, "dpkg inventory").strip().split("\t")
        if len(output) == 2:
            return output[1] if output[0] == "installed" else None
        raise ManagerError("Unexpected dpkg status output")

    def _candidate(self, package, requested):
        if self.windows:
            args = ("--version", requested) if requested else ()
            output = require_success(self._query(
                "winget", "show", *self._win_args(package), "--architecture", "x64", *args,
                "--disable-interactivity"), "WinGet candidate lookup")
            match = re.search(r"(?m)^\s*Version:[ \t]+(\S+)[ \t]*$", output)
            if f"[{package}]" not in output or not match or (requested and match.group(1) != requested):
                raise ManagerError("WinGet metadata did not confirm the exact package and version. English WinGet metadata is required.")
            urls = re.findall(r"(?mi)^[ \t]*Installer Url:[ \t]+(https://\S+)[ \t]*$", output)
            hashes = re.findall(r"(?mi)^[ \t]*Installer SHA256:[ \t]+([0-9a-f]{64})[ \t]*$", output)
            if len(urls) != 1 or len(hashes) != 1:
                raise ManagerError("WinGet did not identify one installer URL and SHA-256. Review the package metadata.")
            return match.group(1), urls[0], hashes[0].lower()
        if requested:
            output = require_success(self._query("apt-cache", "show", f"{package}={requested}"), "APT version lookup")
            if any(re.search(rf"(?m)^Package: {re.escape(package)}$", stanza)
                   and re.search(rf"(?m)^Version: {re.escape(requested)}$", stanza)
                   for stanza in output.strip().split("\n\n")):
                return requested, None, None
        else:
            output = require_success(self._query("apt-cache", "policy", package), "APT policy lookup")
            match = re.search(r"(?m)^\s*Candidate:[ \t]+(\S+)", output)
            if match and match.group(1) != "(none)":
                return match.group(1), None, None
        raise ManagerError(f"No matching APT version for {package}. Check your configured sources and package cache.")

    def _older(self, installed, candidate):
        if self.windows:
            return numeric_version(installed) < numeric_version(candidate)
        result = self._query("dpkg", "--compare-versions", installed, "lt", candidate)
        if result.returncode == 1:
            return False
        require_success(result, "Debian package version comparison")
        return True

    def plan(self, action, tool, requested=None):
        self._check_platform()
        if tool != "kicad":
            raise ManagerError(f"No managed package for {tool}. Use the eSim release for bundled tools.")
        if requested and not re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]*", requested):
            raise ManagerError("Invalid package version")
        if self.windows and requested:
            numeric_version(requested)
        package = PACKAGE_IDS[self.host.os]
        installed = self.installed(package)
        if action == "install" and installed:
            return Plan(action, tool, package, installed, installed, self.host.os, (), "already installed")
        if action == "upgrade" and not installed:
            return Plan(action, tool, package, None, None, self.host.os, (), "not installed in supported scope")
        if self.windows and action == "upgrade" and not self.installed(package, upgrade_available=True):
            return Plan(action, tool, package, installed, None, "winget (machine, x64)", (), "no eligible WinGet upgrade")
        latest = self._candidate(package, None) if self.windows else None
        if latest and (not requested or requested == latest[0]):
            target, installer_url, installer_sha256 = latest
        else:
            target, installer_url, installer_sha256 = self._candidate(package, requested)
        if int(upstream_version(target).split(".")[0]) not in {7, 8, 9, 10}:
            raise ManagerError(f"KiCad {target} is outside the supported netlister major range (7-10). "
                               "Preview an available version within that range with --version.")
        if self.windows:
            numeric_version(target)
            source = "winget (machine, x64)"
            options = ("--no-upgrade",) if action == "install" else ()
            command = self._command("winget", action, *self._win_args(package), "--architecture", "x64",
                                    "--version", target, *options)
        else:
            source = "APT configured sources"
            options = ("--only-upgrade",) if action == "upgrade" else ()
            args = ("--no-remove", *options, "install", f"{package}={target}")
            apt = self._command("apt-get", *args)
            command = self._command("sudo", *apt) if getattr(os, "geteuid", lambda: -1)() != 0 else apt
        status = "available"
        if installed and not self._older(installed, target):
            command, status = (), "up to date or newer"
        return Plan(action, tool, package, installed, target, source, command, status, installer_url, installer_sha256,
                    latest_available=latest[0] if latest else None, requested_version=requested,
                    compatibility="within eSim's documented KiCad 7-10 netlister range",
                    verification="manifest SHA-256 checked by WinGet during install" if self.windows else "APT repository verification")

    def execute(self, plan, discovery):
        fresh = self.plan(plan.action, plan.tool, plan.requested_version)
        if fresh != plan or not fresh.command:
            raise ManagerError("Package state changed. Review a new plan before applying.")
        try:
            result = self.runner(plan.command, timeout=1800, interactive=True)
        except ManagerError as exc:
            raise self._operation_error(plan, cause=exc) from exc
        if result.returncode:
            raise self._operation_error(plan, result=result)
        installed = self.installed(plan.package)
        if installed != plan.target:
            raise ManagerError(f"Package verification expected {plan.target}, found {installed or 'nothing'}")
        if self.windows:
            candidates = [candidate for candidate in discovery.system_tools(plan.tool)
                          if candidate.source == "Program Files KiCad"]
        else:
            path = Path("/usr/bin/kicad-cli")
            output = require_success(self._query("dpkg-query", "-S", str(path)), "KiCad executable ownership lookup")
            if not re.search(rf"(?m)^{plan.package}(?::\S+)?: /usr/bin/kicad-cli$", output):
                raise ManagerError(f"Package ownership for {path} could not be verified")
            candidates = [candidate for candidate in discovery.system_tools(plan.tool) if candidate.path == path]
        expected = numeric_version(upstream_version(plan.target))
        for candidate in candidates:
            result = probe(plan.tool, candidate, None, self.runner)
            version = result.version
            if version and not result.prerelease and numeric_version(version) == expected:
                return {"package_version": installed, "executable": candidate.path, "executable_version": version}
        raise ManagerError(f"Package {installed} is registered, but its executable version could not be verified in the system location")

    def _operation_error(self, plan, **failure):
        try:
            observed = self.installed(plan.package) or "absent"
        except ManagerError:
            observed = "unknown"
        return PackageOperationError(plan, observed, windows=self.windows, **failure)
