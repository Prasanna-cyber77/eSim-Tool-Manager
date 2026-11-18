"""Host identification, subprocess execution, and structured event logging."""

import json
import logging
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


class ManagerError(Exception):
    """An operation failed with a message suitable for the CLI."""


@dataclass(frozen=True)
class Host:
    os: str
    release: str
    architecture: str

    @classmethod
    def detect(cls):
        architecture = platform.machine().lower()
        if sys.platform == "win32":
            return cls("windows", platform.version(), architecture)
        if sys.platform == "linux":
            try:
                release = os.freedesktop_os_release()
            except OSError:
                release = {}
            return cls(release.get("ID", "linux"), release.get("VERSION_ID", ""), architecture)
        return cls(sys.platform, platform.release(), architecture)


def run(argv, *, env=None, timeout=10, interactive=False, cwd=None):
    """Run a resolved executable. Apt may use the terminal after approval."""
    try:
        return subprocess.run(
            [str(arg) for arg in argv], env=env, timeout=timeout, cwd=cwd,
            capture_output=not interactive, stdin=None if interactive else subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", shell=False,
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if os.name == "nt" and not interactive else 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ManagerError(f"{argv[0]} timed out after {timeout}s") from exc
    except OSError as exc:
        raise ManagerError(f"Could not start {argv[0]}: {exc}") from exc


class EventFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            **record.fields,
        }, ensure_ascii=False)


def event_logger(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(EventFormatter())
    logger = logging.Logger("esim_tool_manager", logging.INFO)
    logger.addHandler(handler)
    return logger, handler


def log_event(logger, name, **fields):
    logger.info(name, extra={"fields": fields})
