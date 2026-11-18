# eSim Automated Tool Manager

FOSSEE IIT Bombay Winter Internship 2026 · eSim Screening Task 5

This Python CLI discovers eSim and system tools, probes their versions, manages system KiCad and per-user portable Ngspice, and assesses dependencies for six eSim workflows. It models Windows launcher precedence so a system KiCad installation can be visible while eSim continues using the copy it shipped with.

## Run on Windows

Run from the `eSim-Tool-Manager` directory in PowerShell. Use the existing virtual environment directly. Install the archive decoder into this project environment with the requirements file.

```powershell
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt
& $python -m esim_tool_manager --help
& $python -m esim_tool_manager list
& $python -m esim_tool_manager --esim-root '..\eSim' doctor
& $python -m esim_tool_manager --esim-root '..\eSim' doctor --flow netlisting --verbose
& $python -m esim_tool_manager --esim-root '..\eSim' --json doctor --flow dcosim
& $python -m esim_tool_manager version kicad --system
& $python -m esim_tool_manager env
& $python -m esim_tool_manager env --apply --shell powershell
& $python -m esim_tool_manager verify ngspice
& $python -m esim_tool_manager validate
& $python -m esim_tool_manager install kicad --dry-run
& $python -m esim_tool_manager install ngspice --dry-run
& $python -m esim_tool_manager diagnose kicad
& $python -m esim_tool_manager updates
& $python -m esim_tool_manager updates --tool ngspice
& $python -m unittest discover -s tests -v
```

Point `--esim-root` at an installed eSim Full tree for a complete health report. Without an override, the manager checks an explicit path, the saved setting, `ESIM_HOME`, the working directory, unambiguous sibling directories, and the documented `%SystemDrive%\FOSSEE\eSim` location in that order. Every candidate must contain both `VERSION` and `src/frontEnd/Application.py`; ambiguous sibling candidates are reported instead of guessed.

A source checkout often lacks staged executables. `doctor --flow netlisting` checks only KiCad. Analog simulation and each HDL workflow have their own dependency list. `doctor`, `list`, and `version` accept `--verbose` for details such as checked paths and version banners. JSON includes checked paths under `resolution.checked`. `version ngspice` probes the executable eSim would select, falling back to the manager's portable installation when necessary. A successful portable version probe reports `detected`; `doctor` separately warns when eSim still needs an `ESIM_NGSPICE` override to find it. `version ngspice --system` probes candidates outside the supplied eSim root. `--launcher native|batch|python|inherited` selects an alternate Windows launch projection.

`env` previews the process-scoped environment that points eSim at the selected console simulator. It reports `ESIM_NGSPICE`, the appended `PATH` entry, and `SPICE_LIB_DIR` plus an activation snippet for PowerShell, cmd, or bash. `env --apply` probes an isolated child process; it never writes the parent shell, user PATH, or registry. `verify ngspice` runs a trusted voltage-divider netlist through the selected simulator in a fresh temporary directory, checks the numerical result, timeout, exit code, and artifacts, and writes `verification.json` under the manager state directory only after recording the observed result. `validate` combines root discovery, managed receipt/checksum verification, version probing, and the same isolated simulation. A verified receipt is bound to the tool, executable path, SHA-256, version, circuit identifier, and expected numeric value, so replacing the binary or switching builds invalidates it.

Example excerpt from the reference checkout with KiCad absent:

```text
Basic netlisting (netlisting) | missing | Compatibility: unverified
  kicad: missing | Version: unknown | Path: none
  kicad was not found. Check ESIM_KICAD_CLI or preview a system KiCad install with `install kicad --dry-run`.
```

Illustrative output with KiCad 9.0.3 selected from the eSim bundle:

```text
Basic netlisting (netlisting) | ready | Compatibility: verified
  kicad: detected | Version: 9.0.3 | Path: C:\FOSSEE\eSim\tools\kicad\bin\kicad-cli.exe (bundled, bundled KiCad)
  Version requirements checked. An end-to-end workflow was not run.
```

The Windows package adapter targets system `KiCad.KiCad` at machine scope on Windows 11 x64. By default it reads the latest applicable release from the live WinGet source. Use `--version VERSION` to request an exact available package version. The preview shows the installed version, latest available version, selected target, and whether the target falls within eSim's documented KiCad 7–10 netlister range. If the latest release falls outside that range, select a supported version explicitly. WinGet must have access to the `winget` source and English `winget show` output is currently required. [Microsoft WinGet `show` documentation](https://learn.microsoft.com/en-us/windows/package-manager/winget/show), [eSim netlister notes](https://github.com/FOSSEE/eSim/blob/d86c1891b4e1aced7ce84b910d6fba17025bd87c/src/kicadtoNgspice/KICAD9_NETLISTER.md)

## Run on Ubuntu

Use Python 3.11 or newer on Ubuntu 24.04 or 26.04. Run from the project directory. APT plans use the host's configured sources and current cache.

```bash
python3 -m esim_tool_manager list
python3 -m esim_tool_manager --esim-root "$HOME/eSim" doctor
python3 -m esim_tool_manager install kicad --dry-run
python3 -m esim_tool_manager updates
python3 -m unittest discover -s tests -v
```

Replace `$HOME/eSim` with your eSim root. The APT adapter manages the `kicad` package, compares Debian package versions via `dpkg`, and verifies executable ownership through `dpkg-query`. It requires the existing APT tools and, for an approved operation by a non-root user, `sudo`. Cache freshness and package availability are determined by the host. [Ubuntu APT cache documentation](https://manpages.ubuntu.com/manpages/noble/en/man8/apt-cache.8.html)

## Install or upgrade after review

`install` and `upgrade` preview by default. For a real demonstration, use a disposable VM where the transaction has been approved. On Windows, use `$python` as declared above. On Ubuntu, run the same arguments with `python3`.

```powershell
& $python -m esim_tool_manager install kicad --apply
& $python -m esim_tool_manager version kicad --system
& $python -m esim_tool_manager upgrade kicad --dry-run
& $python -m esim_tool_manager upgrade kicad --apply
```

`--apply` displays a fresh plan and requires typing `y` or `yes` in an interactive terminal. The adapter rechecks that plan after confirmation. WinGet plans include the selected installer URL and SHA-256, so a changed manifest for the same version also requires a new review. The command then performs one exact-version transaction and compares the registered package version and system executable version with the approved target. A cancelled or failed operation has a nonzero exit status. If a native transaction fails, review its output and the observed package version before retrying.

On WinGet error `2316632081` (`0x8A150011`), the installer failed integrity verification and WinGet stopped before running it. Check the diagnostics directory reported by `winget --info`. Refresh metadata with `winget source update --name winget` if needed, then preview a version whose installer URL and SHA-256 match the publisher's release asset. `operations.jsonl` records the original native return code, failure category, expected installer digest, and observed installed package version. [WinGet source update](https://learn.microsoft.com/en-us/windows/package-manager/winget/source#update)

`diagnose kicad --version 10.0.8` reads existing local logs and reports the URL, expected and actual SHA-256 values, file size and recovery actions where available. Use `--json` before `diagnose` for structured output or `--winget-log-dir` to inspect copied logs. The command requires no network access.

On Windows x64, `install ngspice --dry-run` previews the reviewed Ngspice 47 `.7z` release. The approved `--apply` flow downloads from the upstream release, verifies the separately published SHA-256, validates archive entries, checks the console version and publishes to `%LOCALAPPDATA%\eSimToolManager\tools\ngspice\47`. The portable copy supports analog simulation. For use in eSim, set `ESIM_NGSPICE` to its `bin\ngspice_con.exe` in the process that starts eSim. For instructions and troubleshooting, see the [installation guide](docs/INSTALLATION.md). [Ngspice release channel](https://ngspice.sourceforge.io/download.html), [independent archive digest](https://community.chocolatey.org/packages/ngspice/47.0.0)

## Reproducible demo

The read-only demonstration script runs help, inventory, doctor, environment preview, the isolated child probe, `verify`, `validate`, the portable dry run, the update check, and the default suite. It does not install or upgrade anything and does not persist environment changes. `-RealTests` additionally enables the gated managed-Ngspice integration test.

```powershell
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 -RealTests
```

## Configuration and logs

Set a manager default root with `config set esim_root ABSOLUTE_PATH`. `config show` displays existing settings and effective overrides. A change to an existing manager settings file requires interactive approval. The manager reads eSim's `~/.nghdl/config.ini` with INI interpolation. It recognizes `ESIM_NGSPICE`, `ESIM_NGSPICE_GUI`, `ESIM_KICAD_CLI`, `ESIM_IVERILOG`, `ESIM_VVP` and `ESIM_IVERILOG_LIB` from the environment or manager settings. A manager override wins over an inherited value for that probe session. The manager works on a copied environment for its child probes and reads launcher-specific loader variables.

| Platform | Settings | Structured logs |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\eSimToolManager\settings.json` | `%LOCALAPPDATA%\eSimToolManager\operations.jsonl` |
| Ubuntu | `$XDG_CONFIG_HOME/esim-tool-manager/settings.json` or `~/.config/esim-tool-manager/settings.json` | `$XDG_STATE_HOME/esim-tool-manager/operations.jsonl` or `~/.local/state/esim-tool-manager/operations.jsonl` |

Put `--json` before read-only subcommands for automation. Exit status `0` indicates a completed command, `1` a missing or unhealthy targeted check, `2` an error, and `3` a cancelled or interrupted change. `list` succeeds even when its inventory contains missing tools.

## Verification scope

- **Automated tests** use temporary directory layouts and mock WinGet and APT transactions. The default suite contains 102 tests: 101 pass and the real managed-Ngspice integration test is skipped unless `ESIM_TOOL_MANAGER_REAL_TESTS=1` is set. Coverage includes bundled and system KiCad, ngspice resolution, same-version stock and custom layouts, root discovery precedence and ambiguity, process-scoped environment preparation and idempotence, receipt corruption and tampering, simulation success/failure/timeout paths, JSON CLI behavior, launched-program timeouts, unusual version banners, GUI guards, and changed package plans. A real Python child process exercises argument handling and timeout enforcement.
- **Real Ngspice validation** installed the checksum-verified Ngspice 47 archive, confirmed console version 47, ran the built-in voltage-divider batch simulation with the expected 1 V output, and recorded a bound verification receipt. The repeat install preview reports `already installed`. See [testing evidence](docs/TESTING.md) for exact commands and remaining integration work.
- `ready` means the prerequisite checks passed. `verified` for analog simulation means the selected executable ran the representative circuit and produced the expected numeric value; a receipt is required. Netlisting version requirements and the matched Icarus pair use separate classifications. `incompatible` identifies a known mismatch, such as KiCad 6 or GHDL mcode. Co-simulation, NGHDL, NgVeri, and a full eSim GUI workflow remain `unverified` until they are separately executed. Static `d_cosim` files or a matching ngspice banner do not establish runtime compatibility.
- System KiCad and the reviewed Windows Ngspice archive are supported. Portable updates compare against the reviewed catalog entry. The eSim-specific NGHDL simulator and other HDL tools are diagnostic targets. A broken managed archive is reported and requires review of its directory before repair.

See the [architecture](docs/ARCHITECTURE.md) for the exact lookup order, the [architecture report](docs/ARCHITECTURE_REPORT.md) for the submission review, the [compatibility matrix](docs/COMPATIBILITY_MATRIX.md) for claim boundaries, the [troubleshooting guide](docs/TROUBLESHOOTING.md) for recovery steps, the [overnight evidence report](docs/OVERNIGHT_PROGRESS.md), the [final readiness report](docs/FINAL_READINESS_REPORT.md), and the [implementation plan](docs/IMPLEMENTATION_PLAN.md) for the requirement-to-test matrix.

## Submission checklist

- [x] Capture automated results, live dry runs, WinGet failure evidence and a local 7z fixture extraction.
- [x] Capture the approved per-user Windows Ngspice install, console version, receipt verification and isolated analog simulation on the development host.
- [x] Add root discovery, environment previews, runtime verification, JSON output and negative-path tests.
- [ ] Repeat the Ngspice installation in a disposable VM.
- [ ] Capture a confirmed Windows KiCad install, system version and health report once the transfer issue is resolved.
- [ ] Capture an approved upgrade and repeat the installation checks on an Ubuntu VM.
- [ ] Record an analog and d_cosim smoke run against an installed eSim Full tree through the eSim GUI.
- [x] Review the source, docs and test evidence. Exclude local state, logs and `.venv`.
- [x] Initialize a local Git repository and create a clean initial commit; no remote has been configured or pushed.
- [ ] After approval, create the private GitHub repository, publish the local commit and grant the requested reviewer access.
