"""Representative simulation receipts for runtime compatibility.

A receipt is written only after an actual analog simulation produces the
expected numerical result. A version banner or the presence of code-model
files never creates one.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .portable import sha256
from .runtime import ManagerError
from .simulation import CIRCUIT_ID, EXPECTED_VALUE, TOLERANCE

RECEIPT_NAME = "verification.json"
SCHEMA_VERSION = 1


def path_for(config_path):
    return Path(config_path).with_name(RECEIPT_NAME)


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        return None
    return data


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError as exc:
        raise ManagerError(f"Could not record the simulation receipt {path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def executable_digest(path):
    try:
        return sha256(Path(path))
    except OSError:
        return None


def build_receipt(result, executable, tool_version):
    return {
        "schema": SCHEMA_VERSION,
        "tool": "ngspice",
        "circuit": CIRCUIT_ID,
        "status": "verified" if result.verified else "failed",
        "executable": str(Path(executable)),
        "executable_sha256": executable_digest(executable),
        "tool_version": tool_version,
        "expected": result.expected,
        "observed": result.observed,
        "tolerance": result.tolerance,
        "simulation_exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "simulated_at": datetime.now(timezone.utc).isoformat(),
    }


def _same_path(left, right):
    return os.path.normcase(os.path.normpath(os.path.abspath(str(left)))) == os.path.normcase(
        os.path.normpath(os.path.abspath(str(right))))


def check(receipt, executable, tool_version=None):
    """Return (verified, human-readable detail) for one executable."""
    if receipt is None:
        return False, "No representative analog simulation has been recorded."
    if receipt.get("tool") != "ngspice":
        return False, "The recorded simulation receipt belongs to another tool."
    if receipt.get("circuit") != CIRCUIT_ID:
        return False, "The recorded simulation receipt has an untrusted circuit identifier."
    if receipt.get("status") != "verified":
        return False, "The recorded analog simulation did not verify this simulator."
    if not receipt.get("executable_sha256") or not receipt.get("tool_version"):
        return False, "The recorded simulation receipt is missing executable evidence."
    expected = receipt.get("expected")
    observed = receipt.get("observed")
    tolerance = receipt.get("tolerance")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               for value in (expected, observed, tolerance)):
        return False, "The recorded simulation receipt has non-numeric result evidence."
    if not all(math.isfinite(value) for value in (expected, observed, tolerance)) or tolerance < 0:
        return False, "The recorded simulation receipt has invalid result evidence."
    # Use trusted built-in fixture constants instead of trusting values supplied by the receipt
    if abs(expected - EXPECTED_VALUE) > TOLERANCE:
        return False, "The recorded simulation receipt has an untrusted expected value."
    if tolerance > TOLERANCE:
        return False, "The recorded simulation receipt has an untrusted tolerance."
    if receipt.get("simulation_exit_code") != 0:
        return False, "The recorded simulation did not exit successfully."
    if abs(observed - EXPECTED_VALUE) > TOLERANCE:
        return False, "The recorded simulation value was outside its stated tolerance."
    if not executable or not receipt.get("executable") or not _same_path(receipt["executable"], executable):
        return False, "The recorded simulation used a different simulator executable."
    digest = executable_digest(executable)
    if not digest or digest != receipt.get("executable_sha256"):
        return False, "The simulator executable changed after the recorded simulation."
    if tool_version and tool_version != receipt["tool_version"]:
        return False, (f"The recorded simulation used ngspice {receipt['tool_version']}, "
                       f"but the selected executable reports {tool_version}.")
    simulated_at = receipt.get("simulated_at") or "an earlier run"
    return True, (f"Representative analog simulation verified at {simulated_at} "
                  f"(v(out) = {observed}, expected {EXPECTED_VALUE}).")


def stale_summary(receipt):
    if receipt is None:
        return None
    return {"circuit": receipt.get("circuit"), "status": receipt.get("status"),
            "executable": receipt.get("executable"), "tool_version": receipt.get("tool_version"),
            "observed": receipt.get("observed"), "expected": receipt.get("expected"),
            "simulated_at": receipt.get("simulated_at")}
