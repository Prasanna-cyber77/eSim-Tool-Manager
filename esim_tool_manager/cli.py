"""Command-line entry point and formatting."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from . import settings, verification
from .diagnostics import diagnose_install, winget_log_directory
from .discovery import TOOLS, Candidate, Discovery, discover_root
from .environment import prepare as prepare_environment
from .health import FLOWS, FLOW_LABELS, doctor, inventory, probe
from .packages import INTEGRITY_FAILURE, PACKAGE_IDS, PackageManager, PackageOperationError
from .portable import ArchiveIntegrityError, PortableManager
from .runtime import Host, ManagerError, event_logger, log_event
from .simulation import analog_smoke_test


def parser():
    command = argparse.ArgumentParser(prog="python -m esim_tool_manager",
                                      description="Inspect eSim tools and manage KiCad and portable Ngspice.")
    command.add_argument("--esim-root", type=Path, help="eSim installation or source root")
    command.add_argument("--launcher", choices=("native", "batch", "python", "inherited"))
    command.add_argument("--json", action="store_true", help="write a machine-readable report")
    sub = command.add_subparsers(dest="action", required=True)
    listing = sub.add_parser("list", help="show detected executables and versions")
    version = sub.add_parser("version", help="probe one executable version")
    version.add_argument("tool", choices=TOOLS)
    version.add_argument("--system", action="store_true", help="check system KiCad or ngspice candidates")
    check = sub.add_parser("doctor", help="check eSim workflow dependencies")
    check.add_argument("--flow", choices=FLOWS)
    verify = sub.add_parser("verify", help="run a representative analog simulation and record the result")
    verify.add_argument("tool", choices=("ngspice",))
    validate = sub.add_parser("validate", help="run the end-to-end detection, checksum and analog validation")
    environment = sub.add_parser("env", help="preview or apply a process-scoped simulator environment")
    environment.add_argument("--shell", choices=("powershell", "cmd", "bash"),
                             help="activation snippet dialect")
    environment.add_argument("--apply", action="store_true",
                             help="apply to an isolated child process and probe it (no permanent changes)")
    for diagnostic in (listing, version, check, verify, validate, environment):
        diagnostic.add_argument("--verbose", action="store_true", help="show checked paths and version banners")
    updates = sub.add_parser("updates", help="check available KiCad or portable Ngspice versions")
    updates.add_argument("--tool", choices=("kicad", "ngspice"), default="kicad")
    diagnose = sub.add_parser("diagnose", help="inspect recorded package failures without a download")
    diagnose.add_argument("tool", choices=("kicad",))
    diagnose.add_argument("--version", help="show evidence for a specific attempted version")
    diagnose.add_argument("--winget-log-dir", type=Path, help="path to an alternate WinGet log directory")
    for action in ("install", "upgrade"):
        operation = sub.add_parser(action, help=f"preview a system {action}")
        operation.add_argument("tool")
        operation.add_argument("--version", dest="requested")
        mode = operation.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="preview only (default)")
        mode.add_argument("--apply", action="store_true", help="request interactive confirmation and apply")
    config = sub.add_parser("config", help="manage standalone settings")
    config_sub = config.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("show")
    edit = config_sub.add_parser("set")
    edit.add_argument("key", choices=sorted(settings.OVERRIDES | {"esim_root"}))
    edit.add_argument("value")
    return command


def encode(obj):
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj):
        return asdict(obj)
    raise TypeError(type(obj).__name__)


def display(data):
    print(json.dumps(data, default=encode, ensure_ascii=True, indent=2))


def confirm(message):
    if not sys.stdin.isatty():
        raise ManagerError("This change requires confirmation in an interactive terminal.")
    try:
        return input(f"{message} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def print_tools(tools, verbose=False, recommendations=True):
    for name, row in tools.items():
        result = row["resolution"]
        probe_result = row["probe"]
        candidate = result.selected
        path = "none"
        if candidate:
            ownership = "bundled" if candidate.bundled else "external"
            path = f"{candidate.path} ({ownership}, {candidate.source})"
        version = probe_result.version or "unknown"
        if probe_result.prerelease:
            version += " (prerelease)"
        print(f"  {name}: {row['status']} | Version: {version} | Path: {path}")
        if recommendations and row["recommendation"]:
            print(f"    {row['recommendation']}")
        if (managed := row.get("managed")) is not None:
            print(f"    Portable: installed {managed['installed_version'] or 'none'} | "
                  f"available {managed['available_version']} | {managed['verification']}")
        for warning in result.warnings:
            print(f"    {warning}")
        seen = {item.path for item in (candidate, *result.alternatives) if item}
        separate = [item for item in row["system_candidates"] if item.path not in seen]
        if verbose:
            if probe_result.banner:
                print(f"    Banner: {probe_result.banner}")
            if result.checked:
                print("    Searched:")
                for checked in result.checked:
                    print(f"      {checked}")
            for alternative in result.alternatives:
                print(f"    Shadowed: {alternative.path} ({alternative.source})")
            for item in separate:
                print(f"    Separate system candidate: {item.path} ({item.source})")
        elif result.alternatives or separate:
            print(f"    Other candidates: {len(result.alternatives) + len(separate)} (use --verbose)")


def package_command(args, host, discovery, logger, log_path, winget_dir):
    manager = PackageManager(host)
    action = "upgrade" if args.action == "updates" else args.action
    plan = manager.plan(action, getattr(args, "tool", "kicad"), getattr(args, "requested", None))
    log_event(logger, "package_plan", action=action, package=plan.package, target=plan.target, status=plan.status)
    if args.json:
        display(plan)
    else:
        print(f"{plan.package} ({plan.source}): {plan.status}")
        print(f"Installed: {plan.installed or 'none'} | Latest available: {plan.latest_available or 'not checked'}"
              f" | Selected: {plan.target or 'none'}")
        print(f"Compatibility: {plan.compatibility} | Verification: {plan.verification}")
        if plan.command and args.action != "updates":
            command = subprocess.list2cmdline(plan.command) if host.os == "windows" else shlex.join(plan.command)
            print(f"Planned command: {command}")
            if plan.installer_sha256:
                print(f"Installer: {plan.installer_url}")
                print(f"Installer SHA-256: {plan.installer_sha256}")
    if not getattr(args, "apply", False) or not plan.command:
        return 0
    if not confirm(f"Apply {action} for system {plan.package} {plan.target}?"):
        log_event(logger, "package_cancelled", action=action, package=plan.package)
        print("Cancelled.")
        return 3
    log_event(logger, "package_started", action=action, package=plan.package, target=plan.target)
    try:
        verified = manager.execute(plan, discovery)
    except PackageOperationError as exc:
        log_event(logger, "package_failed", action=action, package=plan.package, target=plan.target, source=plan.source,
                  category=exc.category, returncode=exc.returncode, hresult=exc.hresult,
                  observed_version=exc.observed, installation_state=exc.state,
                  installer_url=plan.installer_url, installer_sha256=plan.installer_sha256,
                  native_output=exc.native_output[-4000:], native_log_hint=exc.log_hint, message=str(exc))
        if exc.category == INTEGRITY_FAILURE:
            report = diagnose_install(plan.package, log_path, winget_dir, plan.target)
            if plan.installer_sha256:
                print(f"Expected SHA-256: {plan.installer_sha256}", file=sys.stderr)
            if report["recorded_integrity_failures_at_least"] > 1:
                print("Repeated integrity failures recorded. Pause retries and compare the WinGet logs with the publisher's release asset.",
                      file=sys.stderr)
        raise
    log_event(logger, "package_verified", action=action, package=plan.package, version=verified["package_version"],
              executable=str(verified["executable"]), executable_version=verified["executable_version"])
    print(f"Verified {verified['executable']} version {verified['executable_version']}.")
    return 0


def portable_command(args, host, discovery, logger, store):
    if args.action == "upgrade":
        raise ManagerError("Use install ngspice --dry-run to review the catalog's versioned portable release.")
    manager = PortableManager(host, store, discovery.env)
    plan = manager.plan(getattr(args, "requested", None))
    release = plan.release
    installed_version = release.version if plan.installed_executable else None
    output = {"tool": "ngspice", "backend": "official release archive", "status": plan.status,
              "installed_version": installed_version, "available_version": release.version,
              "installer_url": release.url, "expected_sha256": release.sha256, "digest_source": release.digest_source,
              "executable": plan.installed_executable, "destination": plan.destination,
              "verification": "payload hash checked during an approved installation" if plan.status == "available" else plan.status,
              "compatibility": "standard Ngspice for analog simulation. eSim co-simulation runtime unverified."}
    if args.json:
        display(output)
    else:
        print(f"ngspice (official release archive): {plan.status}")
        print(f"Installed: {installed_version or 'none'} | Available: {release.version}")
        print(f"Executable: {plan.installed_executable or 'none'} | Destination: {plan.destination}")
        print(f"Source: {release.url}")
        print(f"Expected SHA-256: {release.sha256 or 'unverified'} | Digest source: {release.digest_source or 'none'}")
        print(f"Verification: {output['verification']} | Compatibility: {output['compatibility']}")
        if plan.status == "broken managed installation":
            print("The installed files or receipt do not match. Review the displayed directory before repair.")
        elif plan.status == "verification blocked":
            print("An independently authenticated digest is required before download.")
        elif plan.status == "project dependency py7zr unavailable":
            print("Install project dependencies with `python -m pip install -r requirements.txt`.")
    log_event(logger, "portable_plan", tool="ngspice", version=release.version, status=plan.status)
    if plan.status not in {"available", "already installed"}:
        return 1
    if not getattr(args, "apply", False) or plan.status == "already installed":
        return 0
    if not confirm(f"Download, verify and install portable Ngspice {release.version} for this user?"):
        print("Cancelled.")
        return 3

    def version_probe(binary):
        result = probe("ngspice", Candidate(binary, "managed archive staging", False), discovery.child_env())
        if result.error or result.prerelease:
            raise ManagerError(f"Portable Ngspice version probe failed. {result.error or 'Unexpected prerelease.'}")
        return result.version

    log_event(logger, "portable_started", tool="ngspice", version=release.version, expected_sha256=release.sha256)
    try:
        binary, version = manager.execute(plan, version_probe)
    except (ManagerError, OSError) as exc:
        integrity = isinstance(exc, ArchiveIntegrityError)
        log_event(logger, "portable_failed", tool="ngspice", version=release.version, message=str(exc),
                  category=INTEGRITY_FAILURE if integrity else "portable_installation_failure",
                  expected_sha256=release.sha256, actual_sha256=exc.actual if integrity else None)
        raise
    log_event(logger, "portable_verified", tool="ngspice", version=version, executable=str(binary))
    print(f"Verified portable Ngspice {version} at {binary}.")
    print("Set ESIM_NGSPICE to that console executable for the eSim launch that should use it.")
    return 0


def config_command(args, config_path, current, env, logger):
    data, existing = current
    if args.config_action == "show":
        result = {"settings_file": config_path, "settings": data,
                  "effective_overrides": {key: data.get(key, env.get(key)) for key in sorted(settings.OVERRIDES)
                                          if key in data or key in env}}
        if args.json:
            display(result)
        else:
            print(f"Settings file: {config_path} ({'present' if existing else 'absent'})")
            for key, value in (data | result["effective_overrides"]).items():
                print(f"  {key} = {value}")
        return 0
    value = settings.validated_value(args.key, args.value)
    if data.get(args.key) == value:
        print("Setting already matches.")
        return 0
    if existing and not confirm(f"Save {args.key} = {value} in {config_path}?"):
        print("Cancelled.")
        return 3
    settings.save(config_path, data | {args.key: value}, current)
    log_event(logger, "config_saved", key=args.key)
    print(f"Saved {args.key} in {config_path}.")
    return 0


def diagnostic_command(args, host, env, home, log_path):
    if args.winget_log_dir and not args.winget_log_dir.is_dir():
        raise ManagerError(f"WinGet log directory does not exist: {args.winget_log_dir}")
    package = PACKAGE_IDS.get("windows" if args.winget_log_dir else host.os)
    if package is None:
        raise ManagerError("Package diagnostics support Windows and Ubuntu. Use --winget-log-dir for copied Windows logs.")
    directory = args.winget_log_dir or (winget_log_directory(env, home) if host.os == "windows" else None)
    report = diagnose_install(package, log_path, directory, args.version)
    if args.json:
        display(report)
    else:
        for key, label in (("package", "Package"), ("selected_version", "Selected version"),
                           ("package_source", "Package source"), ("installer_url", "Installer URL"),
                           ("expected_sha256", "Expected SHA-256"), ("actual_sha256", "Actual SHA-256"),
                           ("file_size_bytes", "Downloaded bytes"), ("downloader", "Downloader"),
                           ("failure_category", "Failure category"), ("returncode", "Native exit code"),
                           ("log", "Evidence log")):
            value = report[key]
            print(f"{label}: {value if value is not None else 'not recorded'}")
        print(f"Recorded integrity failures for package: at least {report['recorded_integrity_failures_at_least']}")
        for action in report["recovery_actions"]:
            print(f"  - {action}")
    return 0 if report["failure_category"] != "no_recorded_failure" else 1


def selected_console(discovery):
    candidate = discovery.resolve("ngspice").selected
    if candidate is None:
        raise ManagerError("No ngspice executable was selected. Install portable Ngspice or set ESIM_NGSPICE.")
    result = probe("ngspice", candidate, discovery.child_env())
    if result.error or not result.version:
        raise ManagerError(f"Ngspice version probe failed: {result.error or 'unrecognized version banner'}")
    return candidate, result


def managed_state(discovery):
    info = discovery.portable_ngspice
    return info.get("inspection") if info else None


def uses_managed(candidate, info):
    return bool(info and info.get("installed_executable")
                and Path(info["installed_executable"]).resolve() == candidate.path.resolve())


def verify_command(args, discovery, logger, receipt_path):
    candidate, version = selected_console(discovery)
    info = discovery.portable_ngspice
    managed = managed_state(discovery)
    if uses_managed(candidate, info) and (managed is None or not managed.verified):
        raise ManagerError("The managed Ngspice installation failed receipt or checksum validation.")
    plan = prepare_environment(discovery, candidate, version.version, shell="powershell")
    result = analog_smoke_test(candidate.path, plan.environment)
    receipt = verification.build_receipt(result, candidate.path, version.version)
    verification.save(receipt_path, receipt)
    log_event(logger, "simulation_verified" if result.verified else "simulation_failed",
              tool="ngspice", executable=str(candidate.path), version=version.version,
              status=result.status, observed=result.observed, expected=result.expected,
              exit_code=result.exit_code, duration_seconds=result.duration_seconds)
    payload = {
        "tool": "ngspice", "executable": candidate.path, "source": candidate.source,
        "version": version.version, "banner": version.banner,
        "managed": asdict(managed) if managed else None,
        "simulation": asdict(result),
        "compatibility": "verified" if result.verified else "unverified",
        "co_simulation": "unverified",
        "claim_scope": "Isolated standard analog batch simulation only. eSim GUI, NGHDL, d_cosim and mixed-signal remain unverified.",
        "receipt": verification.stale_summary(receipt),
    }
    if args.json:
        display(payload)
    else:
        print(f"ngspice: {candidate.path}")
        print(f"Version: {version.version} | Source: {candidate.source}"
              + (" | Managed receipt and checksums: verified" if managed and managed.verified else ""))
        if managed and managed.problems:
            for problem in managed.problems:
                print(f"  Managed installation: {problem}")
        print(f"Simulation: {result.status} | circuit {result.circuit} | "
              f"v(out) = {result.observed if result.observed is not None else 'not found'} "
              f"(expected {result.expected}) | exit {result.exit_code} | {result.duration_seconds}s")
        for reason in result.reasons:
            print(f"  {reason}")
        print(f"Analog runtime compatibility: {'verified' if result.verified else 'unverified'}")
        print("Co-simulation/NGHDL compatibility: unverified (separate classification; not exercised).")
    return 0 if result.verified else 1


def validate_command(args, discovery, logger, selection, receipt_path):
    report = doctor(discovery, "simulation", verification=verification.load(receipt_path))
    candidate = report["tools"]["ngspice"]["resolution"].selected
    version = report["tools"]["ngspice"]["probe"]
    info = discovery.portable_ngspice
    managed = managed_state(discovery)
    managed_used = uses_managed(candidate, info) if candidate else False
    simulation = None
    receipt = None
    simulation_blocked = ""
    if managed_used and (managed is None or not managed.verified):
        simulation_blocked = ("Not run because the managed Ngspice installation failed receipt or checksum "
                              "validation.")
    elif candidate and version.version and not version.error:
        plan = prepare_environment(discovery, candidate, version.version, shell="powershell")
        simulation = analog_smoke_test(candidate.path, plan.environment)
        receipt = verification.build_receipt(simulation, candidate.path, version.version)
        verification.save(receipt_path, receipt)
        log_event(logger, "validation_simulation", status=simulation.status,
                  observed=simulation.observed, expected=simulation.expected)
    exported = discovery.env.get("ESIM_NGSPICE")
    environment_ready = bool(candidate and exported
                             and os.path.normcase(os.path.abspath(exported)) == os.path.normcase(
                                 os.path.abspath(str(candidate.path))))
    if managed_used:
        managed_status = "verified" if managed and managed.verified else "broken"
        managed_detail = managed.status if managed else "Managed installation state is unavailable."
    else:
        managed_status = "not used" if info and info.get("installed_executable") else "not installed"
        managed_detail = "Another ngspice candidate is selected."
    checks = {
        "esim_root": {"status": "verified" if selection.path else "missing",
                      "detail": selection.method, "path": selection.path},
        "managed_ngspice": {"status": managed_status, "detail": managed_detail},
        "version": {"status": "verified" if version.version and not version.error else "missing",
                    "detail": version.version or version.error or "not detected"},
        "esim_environment": {"status": "configured" if environment_ready else "not exported",
                             "detail": ("ESIM_NGSPICE is exported in this process."
                                        if environment_ready else
                                        "Run `env --apply` for a child process or evaluate its snippet before launching eSim.")},
        "simulation": {"status": simulation.status if simulation else ("blocked" if simulation_blocked else "missing"),
                       "detail": (f"v(out) = {simulation.observed}, expected {simulation.expected}"
                                  if simulation else simulation_blocked or "No console simulator was available.")},
    }
    overall = (selection.path is not None
               and checks["version"]["status"] == "verified"
               and simulation is not None and simulation.verified
               and (not managed_used or managed.verified))
    payload = {"overall": "pass" if overall else "fail", "checks": checks,
               "health": report["flows"]["simulation"], "simulation": asdict(simulation) if simulation else None,
               "managed": asdict(managed) if managed else None,
               "claim_scope": "Isolated analog runtime only; NGHDL, d_cosim, mixed-signal and eSim GUI integration stay separately classified."}
    if args.json:
        display(payload)
    else:
        print("End-to-end validation")
        print(f"eSim root: {selection.path or 'undetected'} ({selection.method})")
        if selection.warning:
            print(f"  {selection.warning}")
        print(f"Managed Ngspice: {checks['managed_ngspice']['status']} ({checks['managed_ngspice']['detail']})")
        print(f"ngspice version: {checks['version']['status']} ({checks['version']['detail']})")
        print(f"eSim environment: {checks['esim_environment']['status']} ({checks['esim_environment']['detail']})")
        if simulation:
            print(f"Analog simulation: {simulation.status} | v(out) = {simulation.observed} "
                  f"(expected {simulation.expected}) | exit {simulation.exit_code} | "
                  f"{simulation.duration_seconds}s")
            for reason in simulation.reasons:
                print(f"  {reason}")
        else:
            print("Analog simulation: missing (no console ngspice)")
        print(f"Overall: {'PASS' if overall else 'FAIL'}")
        if simulation is not None and simulation.verified:
            print("Claim scope: isolated analog runtime verified; NGHDL, d_cosim, mixed-signal and eSim GUI integration remain unverified.")
        else:
            print("Claim scope: isolated analog runtime not verified; NGHDL, d_cosim, mixed-signal and eSim GUI integration remain unverified.")
    return 0 if overall else 1


def environment_command(args, discovery, logger, selection):
    candidate, version = selected_console(discovery)
    shell = args.shell or ("powershell" if discovery.windows else "bash")
    plan = prepare_environment(discovery, candidate, version.version, shell=shell)
    applied = None
    if args.apply:
        probe_result = probe("ngspice", candidate, plan.environment)
        applied = {"verified": bool(probe_result.version) and not probe_result.error,
                   "version": probe_result.version, "error": probe_result.error}
        log_event(logger, "environment_prepared", shell=shell, executable=str(candidate.path),
                  changes=[change.variable for change in plan.changes], applied=applied["verified"])
    payload = {"esim_root": discovery.root, "discovery_method": selection.method,
               "simulator": candidate.path, "source": candidate.source,
               "version": version.version, "prepared": asdict(plan), "applied": applied,
               "scope": "Child process only. The parent shell, PATH and registry are unchanged."}
    if args.json:
        display(payload)
    else:
        print(f"eSim root: {discovery.root or 'undetected'} ({selection.method})")
        print(f"Selected simulator: {candidate.path} | Version: {version.version} | Source: {candidate.source}")
        print("Prepared environment (child process only; parent PATH and registry unchanged):")
        if plan.changes:
            for change in plan.changes:
                if change.variable == "PATH":
                    print(f"  {change.variable}: {change.action} {change.value} (existing PATH preserved)")
                else:
                    previous = change.previous if change.previous is not None else "unset"
                    print(f"  {change.variable}: {change.action} {change.value} (was {previous})")
        else:
            print("  No changes required; the environment is already prepared.")
        for note in plan.notes:
            print(f"  Note: {note}")
        print(f"Activation snippet ({shell}):")
        for line in plan.snippet.splitlines():
            print(f"  {line}")
        if applied is not None:
            state = "verified" if applied["verified"] else f"failed ({applied['error']})"
            print(f"Isolated child probe: ngspice {applied['version'] or 'unknown'} ({state})")
    return 0 if applied is None or applied["verified"] else 1


def dispatch(args, host, env, home, config_path, logger, log_path):
    current = settings.load(config_path)
    if args.action == "config":
        return config_command(args, config_path, current, env, logger)
    data = current[0]
    selection = discover_root(host, env, explicit=args.esim_root, configured=data.get("esim_root"),
                              project_root=Path(__file__).resolve().parent.parent)
    root = selection.path
    store = config_path.parent / "tools"
    discovery = Discovery(host, env, home, root, args.launcher, data, portable_store=store)
    receipt_path = verification.path_for(config_path)
    if args.action == "env":
        return environment_command(args, discovery, logger, selection)
    if args.action == "verify":
        return verify_command(args, discovery, logger, receipt_path)
    if args.action == "validate":
        return validate_command(args, discovery, logger, selection, receipt_path)
    if args.action in {"install", "upgrade", "updates"}:
        if args.tool == "ngspice":
            return portable_command(args, host, discovery, logger, store)
        directory = winget_log_directory(env, home) if host.os == "windows" else None
        return package_command(args, host, discovery, logger, log_path, directory)
    if args.action == "doctor":
        report = doctor(discovery, args.flow, verification=verification.load(receipt_path))
        report["esim_root_discovery"] = {"method": selection.method, "checked": list(selection.checked),
                                         "warning": selection.warning}
        states = {name: value["status"] for name, value in report["flows"].items()}
        log_event(logger, "health_report", flows=states)
        if args.json:
            display(report)
        else:
            print(f"eSim root: {root or 'undetected'} | Launcher: {discovery.launcher} | Discovery: {selection.method}")
            if selection.warning:
                print(f"  Note: {selection.warning}")
            for flow, result in report["flows"].items():
                print(f"\n{FLOW_LABELS[flow]} ({flow}) | {result['status']} | Compatibility: {result['compatibility']}")
                print_tools({name: report["tools"][name] for name in result["dependencies"]},
                            verbose=args.verbose, recommendations=False)
                if "ngspice" in result["dependencies"] and flow in {"simulation", "dcosim", args.flow}:
                    print(f"  ngspice build: {report['ngspice']['flavor']}")
                    print(f"  Runtime: {report['ngspice']['runtime_compatibility']} | "
                          f"{report['ngspice']['runtime_detail']}")
                for issue in result["issues"]:
                    print(f"  {issue}")
                if (result["compatibility_detail"]
                        and result["compatibility_detail"] != report["ngspice"]["runtime_detail"]):
                    print(f"  {result['compatibility_detail']}")
            for note in report["notes"]:
                print(f"  Note: {note}")
        return 0 if all(status == "ready" for status in states.values()) else 1
    if args.action == "version" and args.system:
        if args.tool not in {"kicad", "ngspice"}:
            raise ManagerError("--system supports kicad and ngspice.")
        results = [{"candidate": candidate, "probe": probe(args.tool, candidate, None)}
                   for candidate in discovery.system_tools(args.tool)]
        log_event(logger, "system_version_check", tool=args.tool, candidates=len(results))
        if args.json:
            display(results)
        else:
            for result in results:
                candidate, outcome = result["candidate"], result["probe"]
                status = "detected" if outcome.version else "unverified"
                suffix = " (prerelease)" if outcome.prerelease else ""
                print(f"  {args.tool}: {status} | Version: {outcome.version or 'unknown'}{suffix} | Path: {candidate.path}")
                if outcome.error:
                    print(f"    {outcome.error} Check the executable and its libraries.")
                if args.verbose and outcome.banner:
                    print(f"    Banner: {outcome.banner}")
            if not results:
                print(f"No system {args.tool} executable found.")
        return 0 if any(result["probe"].version for result in results) else 1
    tools = inventory(discovery, (args.tool,)) if args.action == "version" else inventory(discovery)
    log_event(logger, "inventory", tools={name: row["probe"].version for name, row in tools.items()})
    if args.json:
        display(tools)
    else:
        print(f"eSim root: {root or 'undetected'} ({selection.method})")
        print(f"Launcher: {discovery.launcher}")
        print_tools(tools, verbose=args.verbose)
        for note in discovery.warnings:
            print(f"  Note: {note}")
    if args.action == "version":
        row = tools[args.tool]
        return 0 if row["resolution"].selected and not row["probe"].error else 1
    return 0


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if args.json and ((args.action in {"install", "upgrade"} and args.apply)
                      or getattr(args, "config_action", None) == "set"):
        command.error("--json is for read-only commands and package previews")
    host = Host.detect()
    env = dict(os.environ)
    home = Path.home()
    config_path, log_path = settings.locations(host, env, home)
    logger, handler = None, None
    try:
        if args.action == "diagnose":
            return diagnostic_command(args, host, env, home, log_path)
        logger, handler = event_logger(log_path)
        return dispatch(args, host, env, home, config_path, logger, log_path)
    except (ManagerError, OSError, ValueError) as exc:
        if logger:
            log_event(logger, "command_failed", action=args.action, error_type=type(exc).__name__)
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Check package status before retrying an active transaction.", file=sys.stderr)
        if logger:
            log_event(logger, "interrupted", action=args.action)
        return 3
    finally:
        if handler:
            handler.close()
