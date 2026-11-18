"""Version probes and workflow-specific health checks."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import verification as verification_receipts
from .discovery import TOOLS, has_libvvp
from .runtime import ManagerError, run


PROBES = {
    "kicad": ("version", r"^[ \t]*(?:KiCad(?:[- ]CLI)?[ \t]+)?(?:version[ \t]*:?[ \t]+)?v?(\d+(?:\.\d+)+)"),
    "ngspice": ("--version", r"\bngspice(?:\s+version\s*:?\s*|[- \t]+)v?(\d+(?:\.\d+)*)"),
    "iverilog": ("-V", r"Icarus Verilog version\s+(\d+(?:\.\d+)*)"),
    "vvp": ("-V", r"Icarus Verilog runtime version\s+(\d+(?:\.\d+)*)"),
    "verilator": ("--version", r"\bVerilator\s+(\d+(?:\.\d+)*)"),
    "ghdl": ("--version", r"\bGHDL\s+(\d+(?:\.\d+)*)"),
    "gcc": ("--version", r"^.*\bgcc\b.*?\s+(\d+(?:\.\d+)+)"),
    "make": ("--version", r"\bGNU Make\s+(\d+(?:\.\d+)*)"),
}

FLOWS = {
    "netlisting": ("kicad",),
    "simulation": ("ngspice",),
    "verifier": ("iverilog", "vvp"),
    "dcosim": ("ngspice", "iverilog", "vvp"),
    "ngveri": ("ngspice", "iverilog", "verilator", "make", "gcc"),
    "nghdl": ("ngspice", "ghdl", "make", "gcc"),
}

FLOW_LABELS = {
    "netlisting": "Basic netlisting",
    "simulation": "Analog simulation",
    "verifier": "HDL verification",
    "dcosim": "Icarus co-simulation",
    "ngveri": "Legacy NgVeri",
    "nghdl": "NGHDL / VHDL co-simulation",
}

MISSING_HINTS = {
    "kicad": "Check ESIM_KICAD_CLI or preview a system KiCad install with `install kicad --dry-run`.",
    "ngspice": "Check ESIM_NGSPICE and whether the eSim simulator is installed.",
    "iverilog": "Check the bundled Icarus toolchain or ESIM_IVERILOG.",
    "vvp": "Check for vvp beside iverilog or set ESIM_VVP to the matching runtime.",
    "ghdl": "Check the eSim HDL component or the Ubuntu LLVM/GCC GHDL backend.",
}


@dataclass(frozen=True)
class Probe:
    version: str | None = None
    banner: str = ""
    error: str = ""
    backend: str | None = None
    prerelease: bool = False


def windows_console_image(path):
    """Check the PE subsystem before invoking a Windows executable."""
    with path.open("rb") as handle:
        dos = handle.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            return False
        handle.seek(int.from_bytes(dos[60:64], "little"))
        pe = handle.read(24)
        if len(pe) != 24 or pe[:4] != b"PE\0\0" or int.from_bytes(pe[20:22], "little") < 70:
            return False
        header = handle.read(70)
        return (len(header) == 70 and header[:2] in {b"\x0b\x01", b"\x0b\x02"}
                and header[68:70] == b"\x03\0")


def probe(tool, candidate, env, runner=run):
    if candidate is None or tool not in PROBES:
        return Probe()
    flag, pattern = PROBES[tool]
    try:
        name = candidate.path.resolve().stem.lower()
        if name in {"eeschema", "kicad", "pcbnew", "ngspice_gui", "ngspice-gui", "ngspicew"}:
            return Probe(error="A graphical executable was selected. Choose the command-line executable.")
        # KiCad's documented CLI can itself use the Windows GUI subsystem.
        if os.name == "nt" and (tool, name) != ("kicad", "kicad-cli") and not windows_console_image(candidate.path):
            return Probe(error=f"Windows {tool} is not a console executable. Select the console build.")
        result = runner([candidate.path, flag], env=env, timeout=10, interactive=False)
    except (ManagerError, OSError) as exc:
        return Probe(error=str(exc))
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", f"{result.stdout or ''}\n{result.stderr or ''}")
    lines = [line.lstrip("\ufeff").strip() for line in output.splitlines() if line.strip()]
    banner = next((line for line in lines if re.search(pattern, line, re.IGNORECASE)), lines[0] if lines else "")
    match = re.search(pattern, banner, re.IGNORECASE)
    backend = None
    if tool == "ghdl":
        for line in output.lower().splitlines():
            match_backend = re.search(r"\b(mcode|llvm|gcc)\b.*(?:backend|code generator)", line)
            if match_backend:
                backend = match_backend.group(1)
                break
    if result.returncode:
        return Probe(banner=banner[:300], error=f"Version probe exited with {result.returncode}", backend=backend)
    if match is None:
        return Probe(banner=banner[:300], error="Version banner not recognized", backend=backend)
    prerelease = bool(re.match(r"[-.~ ]?(?:rc|alpha|beta|dev)\d*\b", banner[match.end():], re.I))
    return Probe(match.group(1), banner[:300], backend=backend, prerelease=prerelease)


def inventory(discovery, tools=TOOLS, runner=run):
    compiler = discovery.resolve("iverilog").selected if "vvp" in tools else None
    rows = {}
    for tool in tools:
        resolution = discovery.resolve(tool)
        libdirs = (discovery.icarus_libdir(compiler),) if tool == "vvp" else ()
        result = probe(tool, resolution.selected, discovery.child_env(libdirs), runner)
        issue = tool_issue(tool, resolution, result)
        rows[tool] = {"resolution": resolution, "probe": result,
                      "system_candidates": discovery.system_tools(tool) if tool in {"kicad", "ngspice"} else [],
                      "status": issue[0] if issue else "detected", "recommendation": issue[1] if issue else ""}
        if tool == "ngspice":
            rows[tool]["managed"] = discovery.portable_ngspice
    return rows


def tool_issue(tool, resolution, result):
    if not resolution.selected:
        if tool in {"eeschema", "ngspice-gui"}:
            return "missing", "Check the corresponding GUI component if needed."
        hint = MISSING_HINTS.get(tool, "Check the HDL build toolchain and its executable search paths.")
        return "missing", f"{tool} was not found. {hint}"
    if result.error:
        return "unverified", f"{tool}: {result.error} Check the selected executable and its libraries."
    if tool == "ghdl" and result.backend == "mcode":
        return "incompatible", "GHDL mcode cannot link the VHDL server. Use the LLVM or GCC backend."
    if result.version:
        major = int(result.version.split(".")[0])
        if tool == "kicad" and major < 7:
            return "incompatible", "Use KiCad 7 or later for kicadxml export."
        if tool == "verilator" and major < 5:
            return "incompatible", "Use Verilator 5 or later for the NgVeri build."
        if tool == "kicad" and (major > 10 or resolution.warnings):
            return "unverified", "Verify the selected KiCad version and kicadxml export with this launcher."
        if tool == "ghdl" and not result.backend:
            return "unverified", "GHDL backend is unknown. Verify a working LLVM or GCC backend."
        if result.prerelease:
            return "unverified", f"{tool} reports a prerelease. Verify the workflow with that build."
    return None


def doctor(discovery, flow=None, runner=run, verification=None):
    workflows = {flow: FLOWS[flow]} if flow else FLOWS
    tools = inventory(discovery, FLOWS[flow] if flow else TOOLS, runner=runner)
    selected = {name: row["resolution"].selected for name, row in tools.items()}
    console = selected.get("ngspice")
    compiler = selected.get("iverilog")
    vvp = selected.get("vvp")
    prefix = console.path.resolve().parent.parent if console else None
    code_models = prefix / "lib/ngspice" if prefix else None
    libdir = discovery.icarus_libdir(compiler) if "dcosim" in workflows else None
    code_model_files = set()
    if code_models and code_models.is_dir():
        code_model_files = {path.name.lower() for path in code_models.iterdir() if path.is_file()}
    adapter = "ivlng.dll" if discovery.windows else "ivlng.so"
    dcosim_files = {"digital.cm", adapter, "ivlng.vpi"}.issubset(code_model_files)
    libvvp = has_libvvp(libdir)
    ghdl_cm = "ghdl.cm" in code_model_files
    ngveri_cm = "ngveri.cm" in code_model_files
    probe = tools["ngspice"]["probe"] if console else None
    version = probe.version if probe else None
    runtime_verified, runtime_detail = (False, "No console ngspice is selected.")
    if console and probe.error:
        runtime_detail = f"The selected simulator did not pass its version probe. {probe.error}"
    elif console and not version:
        runtime_detail = "The selected simulator did not report a usable version."
    elif console:
        runtime_verified, runtime_detail = verification_receipts.check(verification, console.path, version)
    deps = {key: [(tools[tool]["status"], tools[tool]["recommendation"]) for tool in required
                  if tools[tool]["recommendation"]] for key, required in workflows.items()}
    if console and console.source == "managed Ngspice archive":
        for key in deps.keys() & {"simulation", "dcosim", "ngveri", "nghdl"}:
            deps[key].append(("unverified", "eSim does not search the manager's portable store. "
                              f"Set ESIM_NGSPICE to {console.path} in the process that launches eSim."))
    if "dcosim" in workflows and console and not dcosim_files:
        missing = sorted({"digital.cm", adapter, "ivlng.vpi"} - code_model_files)
        deps["dcosim"].append(("missing", f"Missing {', '.join(missing)} in {code_models}. Select the eSim nghdl-simulator build."))
    if "dcosim" in workflows and compiler and not libvvp:
        deps["dcosim"].append(("missing", f"libvvp is missing at {libdir or 'the Icarus prefix'}. Use Icarus built with --enable-libvvp."))
    if compiler and vvp:
        same_directory = compiler.path.resolve().parent == vvp.path.resolve().parent
        compiler_version, runtime_version = (tools[tool]["probe"].version for tool in ("iverilog", "vvp"))
        if not same_directory or (compiler_version and runtime_version and compiler_version != runtime_version):
            for key in deps.keys() & {"verifier", "dcosim"}:
                deps[key].append(("unverified", "Icarus compiler and runtime paths or versions differ. Select a matched pair."))
    spice_lib = discovery.env.get("SPICE_LIB_DIR") if "ngspice" in tools else None
    if prefix and spice_lib:
        if Path(spice_lib).resolve() != (prefix / "share/ngspice").resolve():
            for key in deps.keys() & {"simulation", "dcosim", "ngveri", "nghdl"}:
                deps[key].append(("unverified", "SPICE_LIB_DIR differs from the selected ngspice prefix. Check its spinit and code-model paths."))
    if "nghdl" in workflows and console and not ghdl_cm:
        deps["nghdl"].append(("missing", f"ghdl.cm is missing in {code_models}. Check the eSim Full simulator."))
    if "ngveri" in workflows and console and not ngveri_cm:
        deps["ngveri"].append(("missing", f"Ngveri.cm is missing in {code_models}. Check the eSim simulator and NgVeri build."))
    if "nghdl" in workflows:
        release = discovery._configured("NGHDL", "RELEASE")
        if not release or not release.is_absolute() or not (release / "src/xspice/icm").is_dir():
            deps["nghdl"].append(("missing", f"NGHDL build tree is missing under {release or discovery.ini_path}. Check [NGHDL] RELEASE."))
    notes = list(discovery.warnings)
    if discovery.windows:
        root = discovery.root
        if root and (root / ".git").exists() and not (root / "tools").is_dir():
            notes.append("This eSim source checkout has no staged Windows tools. Point --esim-root at an installed eSim tree to check its bundled tools.")
        if deps.keys() & {"ngveri", "nghdl"}:
            msys = discovery._configured("COMPILER", "MSYS_HOME")
            if not msys or not msys.is_absolute() or not (msys / "usr/bin/bash.exe").is_file():
                for key in deps.keys() & {"ngveri", "nghdl"}:
                    deps[key].append(("missing", "MSYS2 bash is missing. Check the eSim HDL component and [COMPILER] MSYS_HOME."))
            if "nghdl" in workflows and (not msys or not msys.is_absolute() or not (msys / "usr/bin/mintty.exe").is_file()):
                deps["nghdl"].append(("missing", "MSYS2 mintty is missing. Check the eSim HDL component and [COMPILER] MSYS_HOME."))
    if "simulation" in workflows and console and not code_models.is_dir():
        notes.append("The upstream doctor also checks the ngspice code-model directory for simulation.")
    flows = {}
    for key, issues in deps.items():
        status = next((state for state in ("incompatible", "missing", "unverified")
                       if any(issue[0] == state for issue in issues)), "ready")
        compatibility = "incompatible" if status == "incompatible" else "unverified"
        detail = ""
        if status == "ready":
            if key in {"netlisting", "verifier"}:
                compatibility = "verified"
                detail = "Version requirements checked. An end-to-end workflow was not run."
            elif key == "simulation" and runtime_verified:
                compatibility = "verified"
                detail = runtime_detail
            elif key == "simulation":
                detail = f"Dependencies detected. {runtime_detail}"
            else:
                detail = "Dependencies detected. Run a representative circuit to verify runtime compatibility."
                if runtime_verified and key in {"dcosim", "ngveri", "nghdl"}:
                    detail += " Analog runtime is verified; co-simulation remains separately unclassified."
        flows[key] = {"status": status, "issues": [issue[1] for issue in issues],
                      "dependencies": workflows[key], "compatibility": compatibility, "compatibility_detail": detail}
    flavor = "not checked" if "ngspice" not in tools else "not detected"
    build_kind = "none"
    provenance = "not detected"
    managed_info = tools["ngspice"].get("managed") if "ngspice" in tools else None
    managed_path = (managed_info or {}).get("installed_executable")
    console_is_managed = bool(console and managed_path and Path(managed_path).resolve() == console.path.resolve())
    if console:
        if console_is_managed or console.source == "managed Ngspice archive":
            flavor = "standard Ngspice portable release"
            build_kind = "standard"
            provenance = "manager-managed official upstream Ngspice archive"
        elif ghdl_cm or ngveri_cm:
            flavor = "eSim co-simulation build layout detected"
            build_kind = "nghdl"
            provenance = "eSim NGHDL/NgVeri code models present"
        elif dcosim_files:
            flavor = "d_cosim artifacts detected; eSim build provenance unknown"
            build_kind = "unknown"
            provenance = "static d_cosim artifacts only; not proof of co-simulation compatibility"
        else:
            flavor = "standard or incomplete ngspice layout"
            build_kind = "standard or unknown"
            provenance = "not established"
    return {"launcher": discovery.launcher, "esim_root": discovery.root, "tools": tools, "flows": flows,
            "ngspice": {"flavor": flavor, "build_kind": build_kind, "build_provenance": provenance,
                        "code_models": code_models, "dcosim_files": dcosim_files,
                        "dcosim_artifacts": dcosim_files,
                        "dcosim_artifacts_note": (
                            "d_cosim artifacts are static evidence only; full co-simulation remains unverified."
                            if dcosim_files else ""),
                        "libvvp_directory": libdir, "libvvp_found": libvvp, "spice_lib_dir": spice_lib,
                        "runtime_compatibility": "verified" if runtime_verified else "unverified",
                        "runtime_detail": runtime_detail,
                        "verification_receipt": verification_receipts.stale_summary(verification)},
            "notes": notes}
