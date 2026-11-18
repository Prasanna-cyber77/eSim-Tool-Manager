# eSim Automated Tool Manager - Architecture Report

## 1. Purpose and scope

The manager is a command-line application that detects an eSim installation, selects the tools eSim needs, previews and applies safe package transactions, prepares a process-scoped runtime environment, and verifies that the selected analog simulator can run a representative circuit. It is designed for screening Task 5: reliable tool installation and version management with explicit approval and evidence.

The implementation is Windows-first for the portable Ngspice path and supports Ubuntu through APT package plans. All state-changing operations are previewed first. `install` and `upgrade` execute only when `--apply` is supplied and the operator types `y` at an interactive terminal.

The manager does not modify the eSim source tree, the registry, the persistent user PATH, or vendor installers. A full eSim GUI run and the HDL co-simulation flows remain outside the verified scope and are reported as separate compatibility classifications.

## 2. System context

```text
+-------------------+       +-------------------------------+
| eSim Full tree    |       | eSim Automated Tool Manager   |
| VERSION           |<----->| CLI + health + packages       |
| src/frontEnd/...  |       | discovery + runtime + state   |
+-------------------+       +---------------+---------------+
                                            |
              +-----------------------------+-----------------------------+
              |                             |                             |
     +--------v--------+          +---------v---------+         +---------v---------+
     | WinGet / APT    |          | Managed Ngspice   |         | Process-scoped    |
     | package plans   |          | archive + receipt |         | environment       |
     +-----------------+          +-------------------+         +-------------------+
                                            |
                                  +---------v---------+
                                  | Isolated ngspice  |
                                  | voltage-divider   |
                                  +-------------------+
```

The manager reads eSim metadata but never writes into the checkout. Local state is written under the per-user manager directory, normally `%LOCALAPPDATA%\eSimToolManager` on Windows and the XDG config/state locations on Linux.

## 3. Module architecture

| Module | Responsibility |
| --- | --- |
| `cli.py` | Argument parsing, command dispatch, human/JSON rendering, approval prompts, exit codes. |
| `discovery.py` | eSim root precedence, ambiguity refusal, launcher projections, PATH modelling, tool resolution, managed Ngspice wiring. |
| `health.py` | Probe catalog, flow requirements, inventory, `doctor`, compatibility verdicts, receipt integration. |
| `packages.py` | WinGet and APT adapters, exact package/version/source/scope plans, plan revalidation, installed-state checks. |
| `portable.py` | Reviewed Ngspice archive catalog, HTTPS hash gate, safe archive extraction, atomic publication, managed receipt and file inspection. |
| `settings.py` | Manager-owned settings, validated absolute paths, atomic writes, concurrent-change detection, XDG support. |
| `environment.py` | Process-scoped PATH/`ESIM_NGSPICE`/`SPICE_LIB_DIR` preparation, idempotence, shell snippets, child-process probe. |
| `runtime.py` | Host detection, bounded subprocess execution, UTF-8 handling, rotating JSONL operation logs. |
| `simulation.py` | Trusted voltage-divider fixture, isolated workspace, numeric/error parsing, timeout and artifact checks. |
| `verification.py` | Bound `verification.json` receipts for tool, path, digest, version, circuit, and expected value. |
| `diagnostics.py` | Read-only WinGet and package-manager log parsing for known transfer and integrity failures. |

The modules form an acyclic dependency graph rooted at `runtime.py` and `settings.py`. Command-specific work is orchestrated by `cli.py`; the lower layers do not print or exit.

## 4. eSim root discovery and launcher projection

`Discovery` evaluates candidates in this order:

1. An explicit `--esim-root` value.
2. The saved manager setting.
3. The `ESIM_HOME` environment variable.
4. The current working directory and its ancestors.
5. An unambiguous sibling named `eSim`.
6. The documented `%SystemDrive%\FOSSEE\eSim` location on Windows.

A candidate must be a directory containing both `VERSION` and `src/frontEnd/Application.py`. Multiple equally valid siblings are reported as ambiguous and are never guessed. Canonical paths are deduplicated before selection.

The launcher projection models how eSim would invoke Python: native, batch, python, or inherited. The selected launcher determines the interpreter path used for version probes and simulation, while `PATH` entries are modelled separately. This avoids claiming that a tool is reachable merely because it exists somewhere on disk.

## 5. Package management workflow

1. `updates` reads the configured package source and maps the requested tool to an exact package identifier.
2. `install` and `upgrade` build a plan containing package ID, version, source, scope, architecture, and any reviewed hash.
3. A human-readable preview is printed; JSON emits the same plan.
4. With `--apply`, the operator must confirm at an interactive terminal. Non-interactive runs are refused.
5. The plan is revalidated immediately before execution to prevent a preview/apply mismatch.
6. WinGet and APT are invoked without a shell, with exact version and source arguments.

WinGet integrity failures such as `0x8A150011` (native code `2316632081`) are preserved as evidence. A failed hash check is never bypassed; `diagnose` explains the known failure and points to the log. The manager does not retry hash-failing KiCad installers automatically.

The portable Ngspice path is a separate Windows x64 workflow: download the reviewed archive, verify its SHA-256, extract defensively, probe the console binary, publish atomically, and write a receipt containing every installed file digest. The reviewed Ngspice 47 receipt records 811 file digests.

## 6. Version checking and health model

`version` probes the executable eSim would select and reports the tool, path, source, and detected version. `--system` restricts the probe to system candidates outside the supplied eSim root. `--verbose` adds checked paths and raw version banners.

`health.py` separates three ideas that are often conflated:

- **Presence**: a tool exists and can be probed.
- **Readiness**: the dependencies for a named flow are present.
- **Runtime compatibility**: the representative simulation actually executed and produced the expected result.

`list` inventories every known tool. `doctor` reports per-flow readiness for netlisting, analog simulation, `d_cosim`, NGHDL, and NgVeri. Missing optional tools produce exit `1` but do not prevent other flows from being reported. `incompatible` is reserved for known mismatches such as KiCad 6 or GHDL mcode; `unverified` is used when a flow has not been executed.

## 7. Settings and runtime environment

Settings are manager-owned JSON. Values are validated as absolute paths, and writes use a lock file plus atomic replace. A concurrent modification is detected and refused rather than silently overwritten. The settings file is intentionally not tracked in the repository.

`env` previews process-scoped changes only:

- `ESIM_NGSPICE` points at the selected console simulator.
- `PATH` gains exactly one managed or bundled tool directory.
- `SPICE_LIB_DIR` points at the selected library directory.
- PowerShell, cmd, and bash snippets are rendered for manual use.

`env --apply` creates an isolated child process and proves the variables arrive without mutating the parent shell. Existing values are preserved unless the manager owns them, and repeated preparation is idempotent.

## 8. Runtime verification

`verify ngspice` runs the trusted `voltage-divider-dc-op-v1` fixture in a temporary directory. The circuit, expected value (`v(out) = 1.0`), tolerance, timeout, and parser are fixed in source. The simulation checks:

- executable identity and version;
- process exit status;
- simulator error text;
- expected numeric output within tolerance;
- elapsed time and bounded output capture;
- cleanup of the temporary workspace.

`validate` combines root discovery, managed receipt and checksum inspection, version probing, and the same isolated simulation. A successful run prints `Overall: PASS`.

A `verification.json` receipt is written only after the observed result is recorded. The receipt is bound to the tool, executable path, SHA-256, version, circuit identifier, and expected value. Replacing the binary, switching builds, or presenting a receipt for another tool invalidates it. The receipt is evidence for the analog simulator only; it does not claim GUI, `d_cosim`, NGHDL, or NgVeri compatibility.

## 9. Error handling and exit codes

| Exit | Meaning |
| ---: | --- |
| `0` | Command completed. |
| `1` | A targeted check is missing or unhealthy. |
| `2` | An error prevented the command from completing. |
| `3` | A change was cancelled or interrupted. |

User errors raise `ManagerError` and are rendered as a single line, with `--json` producing a structured error. `KeyboardInterrupt` is mapped to exit `3`. Operation logs are rotating JSONL records under the manager state directory; they include command names, targets, outcomes, and bounded native output, and are excluded from version control.

## 10. Security controls

- Preview-first changes; `--apply` and typed interactive confirmation are both required.
- No shell invocation for package managers or probes; argument lists are passed directly.
- Exact package ID, version, source, scope, and architecture; plan revalidation after approval.
- Reviewed HTTPS hosts only, with redirects rechecked and TLS certificate verification left enabled.
- Archive SHA-256 gate before extraction; bounded download and entry sizes.
- Path traversal, absolute paths, drive-qualified paths, alternate data streams, duplicate entries, Windows device names, and unsafe links are rejected.
- Publication is staged, version-probed, and atomically renamed; the staged tree is removed on failure.
- Managed receipts and installed files are rehashed; missing, extra, modified, or malformed files are reported.
- Settings and receipts are written atomically with restrictive handling; secrets are never stored.
- The reference eSim checkout is read-only; the manager writes only to its own state and temporary directories.
- Logs are bounded and local; no telemetry or network upload exists.

## 11. Testing evidence

Final measured result on the development host:

```text
Ran 102 tests in 4.113s
OK (skipped=1)
```

Per-file totals: 15 CLI/configuration, 16 discovery/health, 19 packages, 17 portable, and 35 runtime features.

The gated real integration test passed against the managed Ngspice 47 installation:

```text
Ran 1 test in 3.282s
OK
```

It verified the receipt and 811 recorded file digests, ran the isolated voltage-divider simulation, and observed `v(out) = 1.0` against the expected `1.0`.

The final audit also smoke-tested `list`, `version`, `doctor`, `verify`, `validate`, `env`, `updates`, `install`, and `diagnose`. `diagnose kicad` reproduced the recorded WinGet integrity failure without executing the rejected installer.

## 12. Known limitations and outstanding blockers

1. **KiCad installation is blocked externally.** WinGet downloads have failed hash verification for KiCad 9.0.3 and 10.0.8. The rejected installers were not executed. No live KiCad netlisting evidence exists.
2. **eSim GUI integration is unverified.** The analog fixture proves the simulator binary and manager orchestration, not the eSim GUI or widget path.
3. **HDL flows are unverified at runtime.** `d_cosim`, NGHDL, and NgVeri need matched Icarus, GHDL, MSYS2, and eSim components.
4. **Ubuntu is mocked, not executed.** APT behavior is covered by tests on the Windows host; no Ubuntu VM transaction has been run.
5. **No disposable-VM repeat.** Managed Ngspice installation and simulation evidence comes from the development host.
6. **No remote repository yet.** A local Git history exists; publishing and reviewer access require explicit approval.

## 13. Reproduction

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest discover -s tests -v
& '.\.venv\Scripts\python.exe' -m esim_tool_manager list
& '.\.venv\Scripts\python.exe' -m esim_tool_manager validate
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 -RealTests
```

The demo script exercises all nine command families and writes no persistent configuration beyond the manager's normal state directory.
