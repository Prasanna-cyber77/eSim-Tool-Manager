"""Isolated analog simulation verification using one built-in trusted netlist.

The command never accepts arbitrary netlists or user-provided commands. It
writes a small voltage-divider circuit into a fresh temporary directory, runs
the selected console simulator in batch mode, and checks a numerical result.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .runtime import ManagerError, run

CIRCUIT_ID = "voltage-divider-dc-op-v1"
EXPECTED_VALUE = 1.0
TOLERANCE = 1e-6
MAX_CAPTURE = 8000

CIRCUIT = """eSim Tool Manager analog smoke test - voltage divider
V1 in 0 dc 3
R1 in out 2k
R2 out 0 1k
.op
.control
op
print v(out)
quit
.endc
.end
"""

VALUE = re.compile(
    r"(?im)^\s*v\(out\)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$"
)
FAILURE = re.compile(r"(?im)^\s*(?:fatal|error)\b")


@dataclass(frozen=True)
class SimulationResult:
    executable: Path
    workspace: Path
    circuit: str
    command: tuple
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    observed: float | None
    expected: float
    tolerance: float
    status: str
    verified: bool
    reasons: tuple
    artifacts: tuple = ()


def _capture(value):
    text = value or ""
    return text if len(text) <= MAX_CAPTURE else text[-MAX_CAPTURE:]


def analog_smoke_test(executable, env, *, runner=run, timeout=30, workspace_parent=None):
    """Run the trusted analog circuit and verify its numerical output."""
    executable = Path(executable)
    if not executable.is_file():
        raise ManagerError(f"Simulator executable does not exist: {executable}")
    parent = str(workspace_parent) if workspace_parent is not None else None
    with TemporaryDirectory(prefix="esim-tool-manager-sim-", dir=parent) as temporary:
        workspace = Path(temporary)
        netlist = workspace / "voltage-divider.cir"
        netlist.write_text(CIRCUIT, encoding="ascii", newline="\n")
        command = (str(executable), "-b", netlist.name)
        started = time.monotonic()
        try:
            result = runner(list(command), env=env, timeout=timeout, interactive=False,
                            cwd=str(workspace))
        except ManagerError as exc:
            return SimulationResult(
                executable, workspace, CIRCUIT_ID, command, None, "", str(exc),
                round(time.monotonic() - started, 6), None, EXPECTED_VALUE, TOLERANCE,
                "error", False, (str(exc),),
            )
        duration = round(time.monotonic() - started, 6)
        stdout = _capture(getattr(result, "stdout", "") or "")
        stderr = _capture(getattr(result, "stderr", "") or "")
        output = f"{stdout}\n{stderr}"
        matches = VALUE.findall(output)
        observed = float(matches[-1]) if matches else None
        reasons = []
        artifacts = ()
        try:
            artifacts = tuple(sorted(path.relative_to(workspace).as_posix()
                                     for path in workspace.rglob("*") if path.is_file()))
        except OSError as exc:
            reasons.append(f"Could not inspect simulation artifacts: {exc}")
        else:
            if netlist.name not in artifacts:
                reasons.append("The trusted netlist is missing after the simulation.")
            unexpected = [name for name in artifacts if name != netlist.name]
            if unexpected:
                reasons.append(f"Unexpected simulation artifacts were created: {', '.join(unexpected)}.")
        if getattr(result, "returncode", None) != 0:
            reasons.append(f"The simulator exited with code {result.returncode}.")
        if observed is None:
            reasons.append("No numeric v(out) value was found in the simulation output.")
        elif abs(observed - EXPECTED_VALUE) > TOLERANCE * max(1.0, abs(EXPECTED_VALUE)):
            reasons.append(f"v(out) was {observed}, expected {EXPECTED_VALUE} within {TOLERANCE}.")
        if FAILURE.search(output):
            reasons.append("The simulator reported an error.")
        verified = not reasons
        return SimulationResult(
            executable, workspace, CIRCUIT_ID, command, getattr(result, "returncode", None),
            stdout, stderr, duration, observed, EXPECTED_VALUE, TOLERANCE,
            "verified" if verified else "failed", verified, tuple(reasons), artifacts,
        )
