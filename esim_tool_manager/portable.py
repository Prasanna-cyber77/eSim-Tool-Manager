"""Install authenticated portable releases in a per-user, versioned store."""

import hashlib
import importlib.util
import json
import re
import shutil
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from .runtime import ManagerError
from .settings import is_esim_root


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str | None
    digest_source: str | None
    prefix: str = "Spice64"
    executable: str = "bin/ngspice_con.exe"


# Download from the upstream project. The separately hosted, moderated
# Chocolatey package embeds this archive and publishes its verification hash.
NGSPICE = Release(
    "47", "https://downloads.sourceforge.net/project/ngspice/ng-spice-rework/47/ngspice-47_64.7z",
    "59225971bd68cdd1199443649aa4615a9e6d684933f205ab49006a3942518f5a",
    "https://community.chocolatey.org/packages/ngspice/47.0.0",
)
RECEIPT = ".install.json"
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_EXTRACTED = 160 * 1024 * 1024


class ArchiveIntegrityError(ManagerError):
    def __init__(self, expected, actual):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Portable archive integrity verification failed. Expected {expected}, got {actual}.")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def trusted_host(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and parsed.port in {None, 443} and (host == "sourceforge.net" or host.endswith(".sourceforge.net")))


class SourceForgeRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not trusted_host(newurl):
            raise ManagerError("The archive download redirected outside the approved HTTPS hosts.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, destination):
    if not trusted_host(url):
        raise ManagerError("The archive URL is outside the approved upstream HTTPS hosts.")
    remaining = MAX_DOWNLOAD
    try:
        with build_opener(SourceForgeRedirects()).open(url, timeout=30) as response, destination.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ManagerError("The archive exceeds the maximum download size.")
                output.write(chunk)
    except (OSError, URLError) as exc:
        raise ManagerError(f"The official archive could not be downloaded: {exc}") from exc


def normalize_7z(archive, intermediate, _extractor):
    # Decode into bounded memory buffers, then construct a tar containing only
    # regular files/directories. Archive names never reach an extraction API.
    try:
        import py7zr
        from py7zr.io import BytesIOFactory
    except ImportError as exc:
        raise ManagerError("Install project dependencies with `python -m pip install -r requirements.txt`.") from exc
    try:
        with py7zr.SevenZipFile(archive, "r", max_extract_size=MAX_EXTRACTED) as source:
            entries = source.list()
            names = set()
            total = 0
            if len(entries) > 20_000 or source.needs_password():
                raise ManagerError("The archive is encrypted or contains excessive entries.")
            for entry in entries:
                name = entry.filename.rstrip("/")
                if (not all(safe_name(part) for part in name.split("/")) or name.casefold() in names
                    or entry.is_symlink or not (entry.is_file or entry.is_directory)):
                    raise ManagerError(f"Unsafe archive entry: {entry.filename!r}")
                names.add(name.casefold())
                total += entry.uncompressed
            if total > MAX_EXTRACTED:
                raise ManagerError("The expanded archive exceeds its size limit.")
            buffers = BytesIOFactory(MAX_EXTRACTED)
            try:
                source.extractall(factory=buffers)
                with tarfile.open(intermediate, "x:") as target:
                    for entry in entries:
                        member = tarfile.TarInfo(entry.filename.rstrip("/"))
                        if entry.is_directory:
                            member.type = tarfile.DIRTYPE
                            target.addfile(member)
                        else:
                            data = buffers.get(entry.filename)
                            if data.size() != entry.uncompressed:
                                raise ManagerError(f"Incomplete archive entry: {entry.filename!r}")
                            data.seek(0)
                            member.size = entry.uncompressed
                            target.addfile(member, data)
            finally:
                buffers.products.clear()
    except ManagerError:
        raise
    except Exception as exc:
        raise ManagerError(f"The verified 7z archive could not be decoded: {type(exc).__name__}: {exc}") from exc


def safe_extract(intermediate, destination, prefix):
    """Extract only ordinary files/directories under the approved archive prefix."""
    total = 0
    names = set()
    if not safe_name(prefix):
        raise ManagerError("Unsafe archive prefix.")
    try:
        if intermediate.stat().st_size > MAX_EXTRACTED:
            raise ManagerError("The expanded archive is too large.")
        with tarfile.open(intermediate, "r:") as members:
            for member in members:
                parts = member.name.rstrip("/").split("/")
                if (parts[0] != prefix or not all(safe_name(part) for part in parts)
                    or not (member.isdir() or member.type in {tarfile.REGTYPE, tarfile.AREGTYPE})
                    or member.sparse is not None):
                    raise ManagerError(f"Unsafe archive entry: {member.name!r}")
                target = destination.joinpath(*parts)
                name = "/".join(parts).casefold()
                if name in names or len(names) >= 20_000:
                    raise ManagerError(f"Duplicate or excessive archive entries: {member.name!r}")
                names.add(name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.size
                if total > MAX_EXTRACTED:
                    raise ManagerError("The expanded archive exceeds its size limit.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with members.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, tarfile.TarError) as exc:
        raise ManagerError(f"The portable archive is corrupt or could not be safely extracted: {exc}") from exc


def safe_name(part):
    return (bool(part) and not part.endswith((".", " "))
            and not re.search(r'[\x00-\x1f<>:"\\|?*]', part)
            and not re.fullmatch(r"(?i)(?:con|prn|aux|nul|conin\$|conout\$|(?:com|lpt)[0-9¹²³])",
                                 part.split(".", 1)[0].rstrip()))


def is_link(path):
    meta = path.lstat()
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (stat.S_ISLNK(meta.st_mode) or bool(getattr(meta, "st_file_attributes", 0)
                                               & reparse_point))


@dataclass(frozen=True)
class ManagedInspection:
    """Evidence collected from one managed installation without trusting it."""

    directory: Path
    status: str
    verified: bool
    binary: Path | None
    receipt_present: bool
    receipt_valid: bool
    expected_version: str
    recorded_version: str | None
    expected_archive_sha256: str | None
    recorded_archive_sha256: str | None
    files_checked: int
    modified_files: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    extra_files: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()


def _inspection(directory, release, *, status, verified=False, binary=None, receipt_present=False,
                receipt_valid=False, recorded_version=None, recorded_archive_sha256=None,
                files_checked=0, modified=(), missing=(), extra=(), links=(), problems=()):
    return ManagedInspection(
        Path(directory), status, verified, binary, receipt_present, receipt_valid, release.version,
        recorded_version, release.sha256, recorded_archive_sha256, files_checked,
        tuple(sorted(modified)), tuple(sorted(missing)), tuple(sorted(extra)), tuple(links),
        tuple(problems),
    )


def inspect_managed(directory, release=NGSPICE):
    """Validate a managed installation's receipt and every recorded file digest."""
    directory = Path(directory)
    binary = directory.joinpath(*PurePosixPath(release.executable).parts)
    if not directory.exists() and not directory.is_symlink():
        return _inspection(directory, release, status="not installed")
    problems = []
    links = []
    for path in (directory, directory.parent, directory.parent.parent):
        try:
            if path.exists() or path.is_symlink():
                if is_link(path):
                    links.append(str(path))
        except OSError as exc:
            problems.append(f"Cannot inspect {path}: {exc}")
    receipt = directory / RECEIPT
    receipt_present = receipt.is_file()
    receipt_linked = False
    if receipt_present:
        try:
            receipt_linked = is_link(receipt)
        except OSError as exc:
            problems.append(f"Cannot inspect {receipt}: {exc}")
        if receipt_linked:
            links.append(str(receipt))
            problems.append("The installation receipt is a filesystem link.")
    if links:
        problems.append("The managed installation uses a filesystem link.")
    if not directory.is_dir():
        problems.append("The managed installation path is not a directory.")
    if not receipt_present:
        problems.append(f"The installation receipt {RECEIPT} is missing.")
    if not release.sha256 or not re.fullmatch(r"[0-9a-f]{64}", release.sha256):
        problems.append("The reviewed catalog has no usable archive digest.")
    data = None
    if receipt_present and not receipt_linked:
        try:
            data = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"The installation receipt cannot be read: {exc}")
    if not isinstance(data, dict):
        if receipt_present and not any("receipt cannot be read" in item for item in problems):
            problems.append("The installation receipt is not a JSON object.")
        return _inspection(directory, release, status="broken managed installation",
                           receipt_present=receipt_present, links=links, problems=problems)
    recorded_version = data.get("version")
    recorded_hash = data.get("archive_sha256")
    expected_files = data.get("files")
    if recorded_version != release.version:
        problems.append(f"The receipt records version {recorded_version!r}, expected {release.version}.")
    if recorded_hash != release.sha256:
        problems.append("The receipt archive digest does not match the reviewed catalog.")
    if not isinstance(expected_files, dict) or not all(
        isinstance(name, str) and isinstance(digest, str) for name, digest in expected_files.items()
    ):
        problems.append("The receipt file manifest is missing or malformed.")
        return _inspection(directory, release, status="broken managed installation",
                           receipt_present=True, recorded_version=recorded_version,
                           recorded_archive_sha256=recorded_hash, links=links, problems=problems)
    try:
        actual = fingerprints(directory)
    except (OSError, ManagerError) as exc:
        problems.append(f"The installed files cannot be hashed: {exc}")
        return _inspection(directory, release, status="broken managed installation",
                           receipt_present=True, recorded_version=recorded_version,
                           recorded_archive_sha256=recorded_hash, links=links, problems=problems)
    names = set(actual) | set(expected_files)
    modified = [name for name in names
                if name in actual and name in expected_files and actual[name] != expected_files[name]]
    missing = [name for name in expected_files if name not in actual]
    extra = [name for name in actual if name not in expected_files]
    if modified:
        problems.append(f"{len(modified)} installed file(s) do not match the receipt.")
    if missing:
        problems.append(f"{len(missing)} recorded file(s) are missing.")
    if extra:
        problems.append(f"{len(extra)} unrecorded file(s) are present.")
    if not binary.is_file():
        problems.append("The managed console executable is missing.")
    verified = not problems and not links
    return _inspection(
        directory, release,
        status="verified install receipt and file checksums" if verified else "broken managed installation",
        verified=verified,
        binary=binary if verified else None, receipt_present=True, receipt_valid=verified,
        recorded_version=recorded_version, recorded_archive_sha256=recorded_hash,
        files_checked=len(actual), modified=modified, missing=missing, extra=extra,
        links=links, problems=problems,
    )


def managed_binary(directory, release=NGSPICE):
    if not release.sha256:
        return None
    inspection = inspect_managed(directory, release)
    return inspection.binary if inspection.verified else None


def fingerprints(directory):
    result = {}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        if is_link(path):
            raise ManagerError("The installed archive contains a filesystem link.")
        if relative != RECEIPT and path.is_file():
            result[relative] = sha256(path)
    return result


@dataclass(frozen=True)
class PortablePlan:
    release: Release
    destination: Path
    decoder: Path
    status: str
    installed_executable: Path | None = None


class PortableManager:
    def __init__(self, host, store, env, *, release=NGSPICE, fetch=download, convert=normalize_7z):
        self.host = host
        self.store = store
        self.env = env
        self.release = release
        self.fetch = fetch
        self.convert = convert

    def plan(self, requested=None):
        if self.host.os != "windows" or self.host.architecture not in {"amd64", "x86_64"}:
            raise ManagerError("Portable Ngspice is available for Windows x64.")
        release = self.release
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", release.version):
            raise ManagerError("The portable catalog has an invalid release version.")
        if requested and requested != release.version:
            raise ManagerError(f"No reviewed portable Ngspice archive for version {requested}. Available: {release.version}.")
        decoder = importlib.util.find_spec("py7zr")
        decoder_path = Path(decoder.origin) if decoder and decoder.origin else Path("py7zr")
        destination = self.store / "ngspice" / release.version
        if (not trusted_host(release.url) or not release.digest_source
            or not re.fullmatch(r"[0-9a-f]{64}", release.sha256 or "")):
            return PortablePlan(release, destination, decoder_path, "verification blocked")
        installed = managed_binary(destination, release)
        if installed:
            return PortablePlan(release, destination, decoder_path, "already installed", installed)
        if destination.exists() or destination.is_symlink():
            return PortablePlan(release, destination, decoder_path, "broken managed installation")
        if not decoder_path.is_absolute() or not decoder_path.is_file():
            return PortablePlan(release, destination, decoder_path, "project dependency py7zr unavailable")
        return PortablePlan(release, destination, decoder_path, "available")

    def execute(self, plan, version_probe):
        if plan != self.plan(plan.release.version) or plan.status != "available":
            raise ManagerError("Portable package state changed or is blocked. Review a new plan.")
        target = plan.destination
        parent = target.parent
        if any(is_esim_root(path) for path in (parent.resolve(), *parent.resolve().parents)):
            raise ManagerError("The portable tool directory cannot be inside an eSim installation or checkout.")
        if any(is_link(path) for path in (self.store, parent) if path.exists() or path.is_symlink()):
            raise ManagerError("The portable store must not be a filesystem link.")
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".staging-", dir=parent) as temporary:
            stage = Path(temporary)
            payload = stage / "download.7z"
            self.fetch(plan.release.url, payload)
            actual = sha256(payload)
            if actual != plan.release.sha256:
                raise ArchiveIntegrityError(plan.release.sha256, actual)
            intermediate = stage / "archive.tar"
            self.convert(payload, intermediate, plan.decoder)
            extracted = stage / "extract"
            safe_extract(intermediate, extracted, plan.release.prefix)
            prefix = extracted / plan.release.prefix
            binary = prefix.joinpath(*PurePosixPath(plan.release.executable).parts)
            if not binary.is_file():
                raise ManagerError("The archive is missing its Ngspice console executable.")
            version = version_probe(binary)
            if version not in {plan.release.version, plan.release.version + ".0"}:
                raise ManagerError(f"The portable executable did not report the approved version {plan.release.version}.")
            receipt = {"version": plan.release.version, "archive_sha256": actual, "files": fingerprints(prefix)}
            with (prefix / RECEIPT).open("x", encoding="utf-8") as output:
                json.dump(receipt, output)
            if target.exists() or target.is_symlink():
                raise ManagerError("The destination appeared during installation. Review a new plan.")
            try:
                prefix.rename(target)
            except OSError as exc:
                raise ManagerError(f"Could not finalize the portable installation: {exc}") from exc
            return target.joinpath(*PurePosixPath(plan.release.executable).parts), version
