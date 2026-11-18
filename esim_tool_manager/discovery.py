"""Model eSim lookup using a snapshot of the environment and filesystem."""

import os
from configparser import ConfigParser, Error as IniError
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .portable import NGSPICE, inspect_managed
from .settings import OVERRIDES, is_esim_root, validated_value


TOOLS = ("kicad", "eeschema", "ngspice", "ngspice-gui", "iverilog", "vvp",
         "verilator", "make", "gcc", "ghdl")

# Sibling discovery is bounded so a deep checkout cannot scan a whole drive.
ROOT_SEARCH_DEPTH = 2


@dataclass(frozen=True)
class Candidate:
    path: Path
    source: str
    bundled: bool


@dataclass(frozen=True)
class Resolution:
    selected: Candidate | None
    alternatives: tuple[Candidate, ...]
    checked: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RootSelection:
    """The selected eSim root plus why it was selected."""

    path: Path | None
    method: str
    checked: tuple[str, ...] = ()
    warning: str | None = None


def _canonical(path):
    return Path(path).expanduser().resolve()


def _valid_root(path):
    try:
        return path.is_dir() and is_esim_root(path)
    except OSError:
        return False


def _sibling_root(base):
    """Return one unambiguous eSim root directly under base."""
    if not base.is_dir():
        return None, ()
    preferred = base / "eSim"
    if _valid_root(preferred):
        return _canonical(preferred), (str(preferred),)
    found = []
    checked = []
    try:
        children = list(base.iterdir())
    except OSError:
        return None, ()
    for child in children:
        if not child.is_dir():
            continue
        checked.append(str(child))
        if _valid_root(child):
            found.append(child)
    unique = {_canonical(candidate) for candidate in found}
    if len(unique) == 1:
        return unique.pop(), tuple(checked)
    return None, tuple(checked)


def discover_root(host, env, explicit=None, configured=None, start=None, project_root=None):
    """Find the eSim root and report how it was selected.

    Precedence: explicit CLI value, manager setting, ESIM_HOME, the current
    directory, unambiguous siblings of the working directory or tool-manager
    project, then the documented Windows default. Every candidate must contain
    eSim's VERSION and src/frontEnd/Application.py.
    """
    checked = []

    def selected(path, method):
        return RootSelection(_canonical(path), method, tuple(checked))

    if explicit is not None:
        value = validated_value("esim_root", os.path.abspath(Path(explicit).expanduser()))
        return selected(value, "explicit --esim-root")
    if configured is not None:
        value = validated_value("esim_root", os.path.abspath(Path(configured).expanduser()))
        return selected(value, "manager setting esim_root")

    home = env.get("ESIM_HOME")
    if home:
        candidate = Path(home).expanduser()
        checked.append(str(candidate))
        if _valid_root(candidate):
            return selected(candidate, "ESIM_HOME environment variable")

    cwd = Path(start or Path.cwd())
    checked.append(str(cwd))
    if _valid_root(cwd):
        return selected(cwd, "current working directory")

    starts = []
    manager_start = _canonical(project_root) if project_root else None
    for path in (cwd, manager_start):
        if path is not None and path not in starts:
            starts.append(path)
    ambiguous = None
    for origin in starts:
        for depth in range(ROOT_SEARCH_DEPTH):
            try:
                base = origin.parents[depth]
            except IndexError:
                break
            if not base.is_dir():
                continue
            candidate, scanned = _sibling_root(base)
            checked.extend(scanned)
            if candidate is not None:
                method = ("sibling of the tool-manager directory"
                          if origin == manager_start else "sibling of the working directory")
                return RootSelection(candidate, method, tuple(checked))
            valid = [path for path in scanned if _valid_root(Path(path))]
            if len({_canonical(Path(path)) for path in valid}) > 1:
                ambiguous = base
                break
        if ambiguous is not None:
            break

    if host.os == "windows":
        fallback = Path(env.get("SystemDrive", "C:") + "/FOSSEE/eSim")
        checked.append(str(fallback))
        if _valid_root(fallback):
            return selected(fallback, "documented Windows installation path")

    if ambiguous is not None:
        return RootSelection(None, "ambiguous sibling candidates", tuple(checked),
                             f"Several eSim roots exist under {ambiguous}. Use --esim-root to choose one.")
    return RootSelection(None, "undetected", tuple(checked))


def find_root(host, env, explicit=None, start=None, project_root=None):
    return discover_root(host, env, explicit=explicit, start=start, project_root=project_root).path


class Discovery:
    def __init__(self, host, env, home, root=None, launcher=None, settings=None, portable_store=None):
        self.host = host
        self.windows = host.os == "windows"
        self.sep = ";" if self.windows else ":"
        self.root = root
        self.portable_store = portable_store if self.windows else None
        self.env = dict(env)
        self.env.update({key: value for key, value in (settings or {}).items() if key in OVERRIDES})
        self.warnings = []
        self.ini_path = home / ".nghdl" / "config.ini"
        self.ini = ConfigParser()
        try:
            self.ini.read(self.ini_path, encoding="utf-8-sig")
        except (IniError, OSError, UnicodeError) as exc:
            self.ini = ConfigParser()
            self.warnings.append(f"Cannot read {self.ini_path}: {exc}")
        default = "native" if root and (root / "eSim.exe").is_file() else "python"
        self.launcher = (launcher or default) if self.windows and root else "inherited"
        self.inherited = [Path(part) for part in env.get("PATH", "").split(self.sep)
                          if part and Path(part).is_absolute()]
        self.system_kicad = self._program_files_kicad()
        self.path_dirs = self._launcher_path()

    def cfg(self, section, key):
        try:
            return self.ini.get(section, key, fallback=None) or None
        except IniError as exc:
            warning = f"{self.ini_path} [{section}] {key}: {exc}"
            if warning not in self.warnings:
                self.warnings.append(warning)
            return None

    def executable(self, path):
        return path.is_absolute() and path.is_file() and (
            path.suffix.lower() == ".exe" if self.windows else os.access(path, os.X_OK))

    def name(self, name):
        return name + (".exe" if self.windows else "")

    def _program_files_kicad(self):
        if not self.windows:
            return []
        base = Path(self.env.get("ProgramFiles", "C:/Program Files")) / "KiCad"
        try:
            return [folder / "bin" for folder in base.iterdir()
                    if self.executable(folder / "bin" / "eeschema.exe")]
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.warnings.append(f"Cannot enumerate {base}: {exc}")
            return []

    def _launcher_path(self):
        dirs = [(path, "inherited PATH") for path in self.inherited]
        if self.launcher == "inherited" or (self.launcher == "python" and self.env.get("ESIM_ENV_READY")):
            return dirs
        tools = self.root / "tools"
        bundled = tools / "kicad/bin"
        custom = tools / "nghdl/install_dir"
        simulator = []
        if self.executable(custom / "bin/ngspice.exe"):
            simulator = [(custom / "bin", "launcher ngspice prefix")]
            if self.launcher != "native":
                lib = (custom / "lib/ngspice", "launcher code-model directory")
                if self.launcher == "batch":
                    simulator.append(lib)
                elif lib[0].is_dir():
                    simulator.insert(0, lib)
            self.env["SPICE_LIB_DIR"] = str(custom / "share/ngspice")
        kicad = [(bundled, "bundled KiCad")] if self.executable(bundled / "eeschema.exe") else []
        system = self.system_kicad
        if self.launcher == "python":
            system = sorted(system, reverse=True, key=str)
        elif self.launcher == "batch":
            system = list(reversed(system))
        stock = tools / "ngspice/bin"
        if self.launcher != "python" or stock.is_dir():
            simulator.append((stock, "stock ngspice directory"))
        return kicad + [(path, "Program Files KiCad") for path in system] + simulator + dirs

    def _collect(self, entries, warnings=()):
        found = []
        checked = []
        seen = set()
        for path, source in entries:
            if path is None or path in seen:
                continue
            seen.add(path)
            checked.append(str(path))
            if self.executable(path):
                bundled = bool(self.root and path.resolve().is_relative_to(self.root.resolve()))
                found.append(Candidate(path, source, bundled))
        return Resolution(found[0] if found else None, tuple(found[1:]), tuple(checked), tuple(warnings))

    def _literal(self, variable):
        value = self.env.get(variable)
        return Path(value) if value and "\x00" not in value else None

    def _configured(self, section, name):
        value = self.cfg(section, name)
        return Path(value) if value and "\x00" not in value else None

    def _search(self, name, dirs=None):
        return [(directory / self.name(name), source)
                for directory, source in (self.path_dirs if dirs is None else dirs)]

    def system_tools(self, tool):
        name = "kicad-cli" if tool == "kicad" else tool
        dirs = [(path, "Program Files KiCad") for path in self._program_files_kicad()] if tool == "kicad" else []
        if self.windows and tool == "ngspice":
            drive = self.env.get("SystemDrive", "C:")
            locations = [Path(drive + "/Spice64/bin"), Path(self.env.get("ProgramFiles", "C:/Program Files")) / "Spice64/bin"]
            dirs.extend((path, "standard Ngspice location") for path in locations)
        dirs += [(path, "inherited PATH") for path in self.inherited]
        if not self.windows:
            dirs.insert(0, (Path("/usr/bin"), "system package directory"))
        entries = self._search(name, dirs)
        if self.windows and tool == "ngspice":
            entries += self._search("ngspice_con", dirs)
        stock = self._collect(entries)
        return [candidate for candidate in (stock.selected, *stock.alternatives)
                if candidate is not None and not candidate.bundled]

    @cached_property
    def portable_ngspice(self):
        if self.portable_store is None:
            return None
        destination = self.portable_store / "ngspice" / NGSPICE.version
        inspection = inspect_managed(destination, NGSPICE)
        path = inspection.binary if inspection.verified else None
        if inspection.verified:
            status = "verified install receipt and file checksums"
        elif inspection.status == "broken managed installation":
            status = "broken managed installation"
        else:
            status = "not installed"
        return {"available_version": NGSPICE.version, "installed_executable": path,
                "installed_version": NGSPICE.version if path else None, "source": NGSPICE.url,
                "verification": status, "inspection": inspection}

    def resolve(self, tool):
        entries = []
        warnings = []
        if tool == "kicad":
            overridden = self.env.get("ESIM_KICAD_CLI")
            if overridden:
                path = self._literal("ESIM_KICAD_CLI")
                entries = [(path, "ESIM_KICAD_CLI")]
                if path and not path.is_absolute() and path.name == str(path):
                    name = path.stem if self.windows and path.suffix.lower() == ".exe" else path.name
                    entries = self._search(name)
                result = self._collect(entries)
                if result.selected is None:
                    return Resolution(None, (), result.checked, (f"ESIM_KICAD_CLI is unusable: {overridden}",))
                return result
            if self.launcher in {"native", "batch"} and len(self.system_kicad) > 1:
                bundled = self.root / "tools/kicad/bin/kicad-cli.exe"
                if not (self.executable(bundled) and self.executable(bundled.with_name("eeschema.exe"))):
                    warnings.append("Multiple system KiCad directories compete. Launcher enumeration order may vary.")
            return self._collect(self._search("kicad-cli"), warnings)
        if tool == "ngspice":
            home = self._configured("NGHDL", "NGHDL_HOME")
            entries = [(self._literal("ESIM_NGSPICE"), "ESIM_NGSPICE"),
                       (home / "install_dir/bin" / self.name(tool) if home else None, "[NGHDL] NGHDL_HOME")]
            warnings.extend(self._invalid_executable_overrides(entries))
            entries.extend(self._search(tool))
            managed = self.portable_ngspice
            if managed and managed["installed_executable"]:
                entries.append((managed["installed_executable"], "managed Ngspice archive"))
            elif managed and managed["verification"] == "broken managed installation":
                warnings.append("Managed Ngspice is broken. Review its installation with `install ngspice --dry-run`.")
            return self._collect(entries, warnings)
        elif tool == "ngspice-gui":
            if not self.windows:
                return self._collect([])
            console = self.resolve("ngspice").selected
            sibling = console.path.resolve().with_name("ngspice_gui.exe") if console else None
            return self._collect([(self._literal("ESIM_NGSPICE_GUI"), "ESIM_NGSPICE_GUI"),
                                  (sibling, "console ngspice prefix")])
        elif tool in {"iverilog", "vvp"}:
            key = tool.upper()
            entries = [(self._literal(f"ESIM_{key}"), f"ESIM_{key}"),
                       (self._configured("COSIM", key), f"[COSIM] {key}")]
            if tool == "vvp":
                compiler = self.resolve("iverilog").selected
                if compiler:
                    entries.append((compiler.path.with_name(self.name(tool)), "beside iverilog"))
            if self.root:
                entries.append((self.root / "library/bin/iverilog/bin" / self.name(tool), "bundled Icarus"))
            entries += self._search(tool)
            if self.windows:
                entries += self._search(tool, [(Path(path), "Windows Icarus default") for path in (
                    "C:/Program Files/iverilog/bin", "C:/Program Files (x86)/iverilog/bin", "C:/iverilog/bin")])
            return self._collect(entries, self._invalid_executable_overrides(entries))
        elif tool in {"verilator", "make", "gcc", "ghdl"}:
            name = "mingw32-make" if tool == "make" and self.windows else tool
            msys = self._configured("COMPILER", "MSYS_HOME") if self.windows else None
            dirs = ([(msys / "mingw64/bin", "[COMPILER] MSYS_HOME")] if msys else []) + self.path_dirs
            entries = self._search(name, dirs)
            if self.windows and tool == "verilator":
                entries += self._search("verilator_bin", dirs)
            return self._collect(entries)
        return self._collect(entries + self._search(tool), self._invalid_executable_overrides(entries))

    def _invalid_executable_overrides(self, entries):
        return [f"Ignoring invalid {source}: {path}" for path, source in entries
                if path is not None and source.startswith("ESIM_") and not self.executable(path)]

    def icarus_libdir(self, compiler):
        choices = (self._literal("ESIM_IVERILOG_LIB"), self._configured("COSIM", "IVERILOG_LIB"))
        for path in choices:
            if path and path.is_absolute() and path.is_dir():
                return path
        if compiler:
            prefix = compiler.path.resolve().parent.parent
            paths = [prefix / "bin", prefix / "lib"] if self.windows else [prefix / "lib"]
            return next((path for path in paths if has_libvvp(path)), paths[-1])
        return None

    def child_env(self, libdirs=()):
        env = dict(self.env)
        env["PATH"] = self.sep.join(str(path) for path, _ in self.path_dirs)
        loader = "PATH" if self.windows else "LD_LIBRARY_PATH"
        parts = [str(path) for path in libdirs if path and path.is_dir()]
        if parts:
            if env.get(loader):
                parts.append(env[loader])
            env[loader] = self.sep.join(parts)
        return env


def has_libvvp(path):
    if path and path.is_dir():
        try:
            return any(entry.is_file() and (entry.name.startswith("libvvp") or entry.name == "vvp.dll")
                       for entry in path.iterdir())
        except OSError:
            return False
    return False
