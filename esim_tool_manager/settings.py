"""Manager-owned settings. eSim configuration is only read by discovery."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .runtime import ManagerError


OVERRIDES = {
    "ESIM_NGSPICE", "ESIM_NGSPICE_GUI", "ESIM_KICAD_CLI",
    "ESIM_IVERILOG", "ESIM_VVP", "ESIM_IVERILOG_LIB",
}
DIRECTORIES = {"esim_root", "ESIM_IVERILOG_LIB"}


def locations(host, env, home):
    if host.os == "windows":
        directory = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / "eSimToolManager"
        return directory / "settings.json", directory / "operations.jsonl"
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "esim-tool-manager"
    state = Path(env.get("XDG_STATE_HOME") or home / ".local" / "state") / "esim-tool-manager"
    return config / "settings.json", state / "operations.jsonl"


def load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (ValueError, OSError) as exc:
        raise ManagerError(f"Cannot read manager settings {path}: {exc}") from exc
    if not isinstance(data, dict) or any(
        key not in OVERRIDES | {"esim_root"} or not isinstance(value, str)
        or not Path(value).is_absolute() or "\x00" in value
        for key, value in data.items()
    ):
        raise ManagerError(f"Invalid settings in {path}. Expected supported keys with absolute paths.")
    return data, path


def validated_value(key, value):
    if key not in OVERRIDES | {"esim_root"}:
        raise ManagerError(f"Unknown setting {key!r}")
    path = Path(value).expanduser()
    if not path.is_absolute() or "\x00" in value:
        raise ManagerError(f"{key} requires an absolute path")
    if key in DIRECTORIES:
        if not path.is_dir():
            raise ManagerError(f"Directory does not exist: {path}")
        if key == "esim_root" and not is_esim_root(path):
            raise ManagerError(f"{path} has no eSim VERSION and src/frontEnd/Application.py")
    elif not path.is_file() or not os.access(path, os.X_OK) or (
        os.name == "nt" and path.suffix.lower() != ".exe"
    ):
        raise ManagerError(f"Executable file required: {path}")
    return str(path)


def is_esim_root(path):
    return (path / "VERSION").is_file() and (path / "src/frontEnd/Application.py").is_file()


def save(path, data, expected):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        lock_handle = lock.open("x")
    except FileExistsError as exc:
        raise ManagerError(f"Settings are locked by another writer: {lock}") from exc
    temp = None
    try:
        if load(path) != expected:
            raise ManagerError("Settings changed. Review the new values before saving.")
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, path)
    finally:
        lock_handle.close()
        lock.unlink(missing_ok=True)
        if temp is not None:
            temp.unlink(missing_ok=True)
