"""Process-scoped environment preparation that never writes permanent state."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Change:
    variable: str
    action: str
    previous: str | None
    value: str


@dataclass(frozen=True)
class PreparedEnvironment:
    simulator: Path
    source: str
    version: str | None
    environment: dict
    changes: tuple
    notes: tuple
    shell: str
    snippet: str

    @property
    def changed(self):
        return bool(self.changes)


def _same_path(left, right):
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(os.path.normpath(str(right)))


def _path_entries(value):
    return [entry for entry in (value or "").split(os.pathsep) if entry]


def _quote_powershell(value):
    return "'" + str(value).replace("'", "''") + "'"


def _quote_bash(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def render_snippet(changes, shell):
    lines = []
    for change in changes:
        name = change.variable
        if shell == "powershell":
            if name == "PATH" and change.action == "prepend":
                lines.append(f"$env:PATH = {_quote_powershell(change.value)} + [IO.Path]::PathSeparator + $env:PATH")
            else:
                lines.append(f"$env:{name} = {_quote_powershell(change.value)}")
        elif shell == "cmd":
            if name == "PATH" and change.action == "prepend":
                lines.append(f'set "PATH={change.value};%PATH%"')
            else:
                lines.append(f'set "{name}={change.value}"')
        else:
            if name == "PATH" and change.action == "prepend":
                lines.append(f"export PATH={_quote_bash(change.value)}:$PATH")
            else:
                lines.append(f"export {name}={_quote_bash(change.value)}")
    if not lines:
        lines = ["# Environment already prepared; no changes required."]
    return "\n".join(lines)


def prepare(discovery, candidate, version=None, shell="powershell"):
    """Build a child-process environment for the selected console simulator.

    Existing variables are copied unchanged. Only ESIM_NGSPICE is set, the
    simulator's bin directory is prepended to PATH when absent, and
    SPICE_LIB_DIR is filled only when it is unset and the prefix ships one.
    """
    executable = Path(candidate.path).resolve()
    prefix = executable.parent.parent
    environment = dict(discovery.env)
    changes = []
    notes = []

    current_simulator = environment.get("ESIM_NGSPICE")
    if not current_simulator or not _same_path(current_simulator, executable):
        changes.append(Change("ESIM_NGSPICE", "set", current_simulator or None, str(executable)))
        environment["ESIM_NGSPICE"] = str(executable)
    else:
        notes.append("ESIM_NGSPICE already points at the selected simulator.")

    bin_dir = str(executable.parent)
    entries = _path_entries(environment.get("PATH"))
    if not any(_same_path(entry, bin_dir) for entry in entries):
        changes.append(Change("PATH", "prepend", environment.get("PATH") or None, bin_dir))
        environment["PATH"] = bin_dir + (os.pathsep + environment["PATH"] if environment.get("PATH") else "")
    else:
        notes.append("The simulator bin directory is already on PATH.")

    share = prefix / "share" / "ngspice"
    if share.is_dir():
        current_lib = environment.get("SPICE_LIB_DIR")
        if not current_lib:
            changes.append(Change("SPICE_LIB_DIR", "set", None, str(share)))
            environment["SPICE_LIB_DIR"] = str(share)
        elif not _same_path(current_lib, share):
            notes.append(f"Existing SPICE_LIB_DIR is preserved: {current_lib}")
        else:
            notes.append("SPICE_LIB_DIR already points at the selected prefix.")
    else:
        notes.append(f"No share/ngspice directory exists under {prefix}; SPICE_LIB_DIR was left unchanged.")

    return PreparedEnvironment(
        executable, candidate.source, version, environment, tuple(changes), tuple(notes),
        shell, render_snippet(changes, shell),
    )
