"""Read recorded package failures without invoking a package manager."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .packages import INTEGRITY_FAILURE, WINGET_HASH_MISMATCH


HASH_LINE = re.compile(
    r"Package hash verification failed\. SHA256 in manifest \[([0-9a-f]{64})\] "
    r"does not match download \[([0-9a-f]{64})\] with file size \[(\d+)\]", re.I
)
DOWNLOAD_LINE = re.compile(r"\[CORE\]\s+(\w+) downloading from url: (https://\S+)")


@dataclass(frozen=True)
class Failure:
    version: str | None
    source: str | None
    installer_url: str | None
    expected_sha256: str | None
    category: str
    log: Path
    actual_sha256: str | None = None
    file_size_bytes: int | None = None
    downloader: str | None = None
    returncode: int | None = None


def winget_log_directory(env, home):
    local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    return local / "Packages/Microsoft.DesktopAppInstaller_8wekyb3d8bbwe/LocalState/DiagOutputDir"


def winget_failures(directory, package):
    failures = []
    # WinGet log names include the session start time. Limit reads to recent logs.
    recent = sorted(directory.glob("WinGet-*.log"), reverse=True)[:100]
    for path in recent:
        try:
            with path.open(encoding="utf-8-sig", errors="replace") as stream:
                log = stream.read(2_000_000)
        except OSError:
            continue
        ident = re.search(r"\bApp id: (\S+)", log)
        manifest = re.search(r"Manifest fields: Name \[[^\]\r\n]+\], Version \[([^\]\r\n]+)\]", log)
        mismatch = HASH_LINE.search(log)
        if not ident or ident[1].casefold() != package.casefold() or not manifest:
            continue
        exit_line = re.search(r"Terminating context: (0x[0-9a-f]+)", log[mismatch.end():] if mismatch else log, re.I)
        code = int(exit_line[1], 16) if exit_line else None
        if not mismatch and code != WINGET_HASH_MISMATCH:
            continue
        # Take only the source flag from the invocation. Other arguments can contain sensitive values.
        invocation = re.search(r"WinGet invoked with arguments: ([^\r\n]+)", log)
        source = re.search(r"'(?:--source|-s)'\s+'([^']+)'", invocation[1]) if invocation else None
        downloads = DOWNLOAD_LINE.findall(log, endpos=mismatch.start() if mismatch else len(log))
        downloader, url = downloads[-1] if downloads else (None, None)
        failures.append(Failure(manifest[1], source[1] if source else None, url, mismatch[1].lower() if mismatch else None,
                                INTEGRITY_FAILURE, path, mismatch[2].lower() if mismatch else None,
                                int(mismatch[3]) if mismatch else None, downloader, code))
    return failures


def manager_failures(log_path, package):
    failures = []
    for path in (log_path.with_name(log_path.name + ".2"), log_path.with_name(log_path.name + ".1"), log_path):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("event") == "package_failed" and entry.get("package") == package:
                failures.append(Failure(entry.get("target"), entry.get("source"), entry.get("installer_url"),
                                        entry.get("installer_sha256"), entry.get("category", "package_manager_failure"),
                                        path, returncode=entry.get("returncode")))
    return list(reversed(failures))


def diagnose_install(package, log_path, winget_dir=None, version=None):
    native = winget_failures(winget_dir, package) if winget_dir else []
    recorded = manager_failures(log_path, package)
    # One attempt can appear in both histories, so use a conservative lower bound.
    repeated = max(len(native), sum(entry.category == INTEGRITY_FAILURE for entry in recorded))
    failure = next((item for item in native + recorded if version is None or item.version == version), None)
    if failure is None:
        recovery = ["Use install kicad --dry-run to inspect current package metadata without downloading."]
        if winget_dir:
            recovery.append("Use winget --info to find its diagnostic logs and pass that directory with --winget-log-dir.")
    elif failure.category == INTEGRITY_FAILURE:
        recovery = ["Keep hash verification enabled. Compare the recorded URL and expected digest with the publisher's release asset.",
                    "If the digest agrees, investigate the transfer on a trusted connection before approving another attempt."]
        if repeated > 1:
            recovery.insert(0, "Pause installation retries and keep the WinGet logs for comparison.")
    else:
        recovery = ["Review the package-manager logs and the observed installed version before retrying."]
    return {"package": package, "selected_version": failure.version if failure else version,
            "package_source": failure.source if failure else None,
            "installer_url": failure.installer_url if failure else None,
            "expected_sha256": failure.expected_sha256 if failure else None,
            "actual_sha256": failure.actual_sha256 if failure else None,
            "file_size_bytes": failure.file_size_bytes if failure else None,
            "downloader": failure.downloader if failure else None,
            "failure_category": failure.category if failure else "no_recorded_failure",
            "returncode": failure.returncode if failure else None,
            "log": failure.log if failure else None, "recorded_integrity_failures_at_least": repeated,
            "recovery_actions": recovery}
