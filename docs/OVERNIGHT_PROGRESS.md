# Overnight progress report

Date: 2026-11-18 (development host timezone UTC-05:00; example receipt timestamp 2026-11-17T18:48:24Z, refreshed by later verify/validate runs)

Workspace: `D:\VS Code\FOSSEE\eSim-Tool-Manager`
Reference (read-only): `D:\VS Code\FOSSEE\eSim`

## Outcome

The manager now completes the requested detection, configuration, runtime-verification, and reporting path on the development host:

- eSim 2.6 source root is discovered automatically as an unambiguous sibling.
- Managed Ngspice 47 is detected from its verified receipt and all 811 recorded file digests.
- `env` produces PowerShell, cmd, and bash child-environment previews without permanent mutation.
- `env --apply` probes a real isolated child process.
- `verify ngspice` runs the trusted voltage-divider circuit and records a bound success receipt.
- `validate` reports `Overall: PASS`.
- The default suite runs 102 tests: 101 pass and one real-integration test is skipped by design. The skipped real test passes when enabled.

No package installation, upgrade, deletion, registry change, PATH change, remote push, or email submission was performed overnight. The reference eSim repository was not modified.

## Test counts

| Run | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 102 run, 101 passed, 1 skipped, 0 failures |
| `ESIM_TOOL_MANAGER_REAL_TESTS=1` + real integration test | 1 run, 1 passed |
| Baseline before new work | 66 run, 66 passed |

## Final audit and documentation pass

The final review smoke-tested all nine CLI commands (`list`, `version`, `doctor`, `verify`, `validate`, `env`, `updates`, `install`, `diagnose`) and fixed audit findings: unused imports in `cli.py` and `discovery.py`, a dead `_quote_cmd` helper in `environment.py`, Windows-only console glyphs replaced with ASCII in `packages.py`, and `portable.is_link` now tolerates platforms without `stat.FILE_ATTRIBUTE_REPARSE_POINT`. Verification receipts are additionally bound to the `tool` field. Stale test counts in the README and docs were replaced with the measured 102/101/1 result.

## Actual command evidence

### Discovery and inventory

```text
> python -m esim_tool_manager list
eSim root: D:\VS Code\FOSSEE\eSim (sibling of the tool-manager directory)
Launcher: python
  ngspice: detected | Version: 47 | Path: C:\Users\Prasanna\AppData\Local\eSimToolManager\tools\ngspice\47\bin\ngspice_con.exe (external, managed Ngspice archive)
    Portable: installed 47 | available 47 | verified install receipt and file checksums
```

### Runtime verification

```text
> python -m esim_tool_manager verify ngspice
ngspice: C:\Users\Prasanna\AppData\Local\eSimToolManager\tools\ngspice\47\bin\ngspice_con.exe
Version: 47 | Source: managed Ngspice archive | Managed receipt and checksums: verified
Simulation: verified | circuit voltage-divider-dc-op-v1 | v(out) = 1.0 (expected 1.0) | exit 0 | 0.283943s
Analog runtime compatibility: verified
Co-simulation/NGHDL compatibility: unverified (separate classification; not exercised).
```

### End-to-end validation

```text
> python -m esim_tool_manager validate
End-to-end validation
eSim root: D:\VS Code\FOSSEE\eSim (sibling of the tool-manager directory)
Managed Ngspice: verified (verified install receipt and file checksums)
ngspice version: verified (47)
eSim environment: not exported (Run `env --apply` for a child process or evaluate its snippet before launching eSim.)
Analog simulation: verified | v(out) = 1.0 (expected 1.0) | exit 0 | 0.286734s
Overall: PASS
Claim scope: isolated analog runtime verified; NGHDL, d_cosim, mixed-signal and eSim GUI integration remain unverified.
```

### Process-scoped environment

```text
> python -m esim_tool_manager env --apply --shell powershell
Selected simulator: ...\ngspice\47\bin\ngspice_con.exe | Version: 47 | Source: managed Ngspice archive
Prepared environment (child process only; parent PATH and registry unchanged):
  ESIM_NGSPICE: set ...\ngspice_con.exe (was unset)
  PATH: prepend ...\ngspice\47\bin (existing PATH preserved)
  SPICE_LIB_DIR: set ...\ngspice\47\share\ngspice (was unset)
Isolated child probe: ngspice 47 (verified)
```

### Doctor with eSim wiring exported

```text
> $env:ESIM_NGSPICE = '...\ngspice\47\bin\ngspice_con.exe'
> python -m esim_tool_manager doctor --flow simulation
Analog simulation (simulation) | ready | Compatibility: verified
  ngspice build: standard Ngspice portable release
  Runtime: verified | Representative analog simulation verified at 2026-11-17T18:48:24.734941+00:00 (v(out) = 1.0, expected 1.0).
```

Without exporting `ESIM_NGSPICE`, the same command intentionally reports the workflow as `unverified` because eSim would not find the private store. This is a wiring distinction, not a simulation failure.

### Dry runs and updates

```text
> python -m esim_tool_manager install ngspice --version 47 --dry-run
ngspice (official release archive): already installed
Installed: 47 | Available: 47
Executable: ...\ngspice\47\bin\ngspice_con.exe
Expected SHA-256: 59225971bd68cdd1199443649aa4615a9e6d684933f205ab49006a3942518f5a
Verification: already installed | Compatibility: standard Ngspice for analog simulation. eSim co-simulation runtime unverified.

> python -m esim_tool_manager updates --tool ngspice
ngspice (official release archive): already installed
Installed: 47 | Available: 47
```

### Negative checks

```text
> python -m esim_tool_manager version ngspice --system
No system ngspice executable found.
(exit 1)
```

`install ngspice --version 47 --dry-run` exits 0 because the reviewed version is already installed. Tests cover missing tools, invalid roots, corrupt receipts, modified managed files, unsafe archives, invalid checksums, timeouts, wrong simulation values, stale receipts, and rejected package plans.

## Files changed or added

- `esim_tool_manager/discovery.py`: `RootSelection`, precedence-based `discover_root`, sibling/ambiguity handling, canonical roots, managed inspection in `portable_ngspice`.
- `esim_tool_manager/portable.py`: `ManagedInspection`, `inspect_managed`, verified-state propagation; `managed_binary` now shares the same inspection.
- `esim_tool_manager/verification.py`: atomic receipt load/save, executable digest, receipt construction, path/version/digest checks, compact summary.
- `esim_tool_manager/environment.py`: process-scoped environment preparation, idempotence, preservation rules, shell snippets.
- `esim_tool_manager/simulation.py`: trusted netlist, isolated workspace, numeric parsing, timeout/error capture, bounded output.
- `esim_tool_manager/runtime.py`: optional working directory for isolated execution.
- `esim_tool_manager/health.py`: runtime receipt integration, build-kind/provenance, managed-path classification through environment overrides, separate compatibility verdicts.
- `esim_tool_manager/cli.py`: `env`, `verify`, `validate`; root/method reporting; JSON guard adjustment; runtime display.
- `tests/test_runtime_features.py`: 35 tests, including one gated real integration test.
- `README.md`, `docs/ARCHITECTURE.md`, `docs/INSTALLATION.md`, `docs/TESTING.md`: updated commands, evidence, claims, and limits.
- `docs/COMPATIBILITY_MATRIX.md`, `docs/TROUBLESHOOTING.md`, `docs/SUBMISSION_CHECKLIST.md`: new.
- `docs/OVERNIGHT_PROGRESS.md`, `scripts/demo.ps1`: new report and reproducible demonstration.
- Runtime evidence outside the source tree: `%LOCALAPPDATA%\eSimToolManager\verification.json` and `operations.jsonl` (not to be committed).

## Blockers and risks

1. **KiCad installation remains blocked externally.** WinGet downloaded short files for 9.0.3 and 10.0.8; hashes did not match. The rejected installers were not executed. No live KiCad netlisting evidence exists.
2. **eSim GUI integration is unverified.** No installed eSim Full tree was available or launched. The analog fixture proves the simulator binary, not eSim's GUI or widget path.
3. **Co-simulation/HDL remains unverified.** `d_cosim` artifacts are present but static. No matched Icarus/libvvp, GHDL backend, MSYS2, `ghdl.cm`, or `ngveri.cm` runtime execution was performed.
4. **Ubuntu is unverified at runtime.** APT behavior is covered only by mocked tests; no VM transaction was run.
5. **No disposable-VM repeat.** The managed install and simulation evidence comes from the development host.
6. **Remote repository not configured.** A local Git repository and clean initial commit now exist, but no remote, push, or reviewer access has been created. Publish only after explicit approval.
7. **Default suite has one intentional skip.** The real test must be run explicitly to include the managed binary; the count in CI without the environment variable will be 101 passed / 1 skipped.

## Submission readiness

Ready for review: source, tests, default-suite results, root discovery, environment preview, isolated analog receipt, dry runs, documentation, compatibility boundaries, and troubleshooting.

Not ready to claim: KiCad installation/netlisting, eSim GUI analog workflow, `d_cosim`, NGHDL, NgVeri, Ubuntu runtime, or disposable-VM reproduction.

## Next action requiring approval

Approve one of the following, then provide the result or environment for evidence capture:

1. A disposable Windows x64 VM Ngspice installation + `verify` + eSim analog GUI run, or
2. A trusted KiCad installer/VM for a confirmed WinGet transaction and live netlist export.

Until approval, do not retry the hash-failing KiCad installers, do not modify the reference eSim checkout, and do not push or submit.
