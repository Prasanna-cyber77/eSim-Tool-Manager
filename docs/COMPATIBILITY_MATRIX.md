# Compatibility matrix

This matrix records evidence, not intentions. A status is upgraded only after the named command or execution path succeeds on the stated platform. "Verified" never includes an unexercised GUI or adapter.

| Area | Status | Evidence and boundary |
| --- | --- | --- |
| Windows 11 x64 CLI | Verified | Python 3.13 virtual environment; `list`, `doctor`, `env`, `verify`, `validate`, `updates`, `install --dry-run`, `version --system`, and the test suite ran on the development host. |
| eSim root discovery | Verified | Unambiguous sibling `..\eSim` was selected from the tool-manager project; explicit, configured, `ESIM_HOME`, current-directory, sibling and fallback precedence are covered by tests. Ambiguous siblings are rejected, not guessed. |
| eSim source checkout | Verified read-only | `D:\VS Code\FOSSEE\eSim` reports version 2.6 from `VERSION` and `src/frontEnd/Application.py`. It is a source checkout without a staged `tools` tree, so bundled-tool checks are limited. |
| Installed eSim Full tree | Unverified | No installed eSim Full tree was available for bundled KiCad, custom ngspice, `d_cosim`, NGHDL, NgVeri, or GUI workflow testing. |
| Managed Ngspice 47 installation | Verified | Receipt and 811 file digests checked; console probe reports `47`. Installed path: `%LOCALAPPDATA%\eSimToolManager\tools\ngspice\47`. |
| Standard analog batch simulation | Verified (isolated) | Built-in `voltage-divider-dc-op-v1` exited 0 and produced `v(out) = 1.0`, expected `1.0`, within `1e-6`. The receipt binds the executable path, SHA-256, version and result. |
| eSim analog GUI workflow | Unverified | eSim itself was not launched. `doctor --flow simulation` remains `unverified` until `ESIM_NGSPICE` is exported for the eSim process; that is wiring evidence, not a GUI run. |
| KiCad detection and netlisting | Unverified | No system or bundled KiCad installation was available. Version gates for KiCad 7-10 are unit-tested, but no live `kicadxml` export was executed. |
| Windows KiCad package installation | Blocked externally | WinGet downloads for 9.0.3 and 10.0.8 failed hash verification. The rejected installers were not executed. This is an unresolved host transfer issue, not a manager success. |
| Portable Ngspice download/install | Verified previously | The approved checksum-verified archive was installed and the repeat preview reports `already installed`. The current run did not repeat a real install. |
| Portable Ngspice update check | Verified | `updates --tool ngspice` reports installed 47 and reviewed available 47. |
| Icarus `d_cosim` | Unverified | Static `d_cosim` artifacts exist in the archive, which is not runtime proof. No matched Icarus compiler/runtime, `libvvp`, or co-simulation execution was verified. |
| NGHDL / VHDL co-simulation | Unverified | No `ghdl.cm`, GHDL backend, MSYS2/NGHDL build tree, or VHDL workflow was exercised. |
| Legacy NgVeri | Unverified | No `ngveri.cm`, Verilator/compiler chain, or NgVeri workflow was exercised. |
| Ubuntu 24.04 / 26.04 | Unverified | APT and discovery behavior are covered by mocked tests only. No Ubuntu VM, real APT transaction, or Ubuntu eSim installation was executed. |
| Environment mutation | Verified absent | Tests compare `os.environ` before and after preparation; `env --apply` probes only a child process. No parent PATH, registry, or eSim configuration was changed. |
| Integrity controls | Verified by tests | Archive digest mismatch, interrupted download, unsafe archive entries, corrupt receipt, modified managed files, and stale plans are negative-tested. Rejected KiCad installers were never executed. |

## Status definitions

- **Verified**: the named command or execution completed on the named platform and produced the stated evidence.
- **Unverified**: dependencies or artifacts may exist, but the actual runtime path was not executed.
- **Missing**: a required prerequisite was not detected.
- **Incompatible**: a known version or backend mismatch was detected.
- **Blocked externally**: work could not proceed because a host or approval condition was unresolved; no unsafe workaround was used.

## Reproduce the key claims

```powershell
$python = '.\.venv\Scripts\python.exe'
& $python -m esim_tool_manager list
& $python -m esim_tool_manager doctor --flow simulation
& $python -m esim_tool_manager verify ngspice
& $python -m esim_tool_manager validate
& $python -m esim_tool_manager env --apply --shell powershell
$env:ESIM_TOOL_MANAGER_REAL_TESTS = '1'
& $python -m unittest tests.test_runtime_features.RealManagedNgspiceTests -v
Remove-Item Env:ESIM_TOOL_MANAGER_REAL_TESTS
```

To see the eSim-wiring state instead of the isolated runtime receipt, run `env`, evaluate its snippet in the shell that will start eSim, and then repeat `doctor --flow simulation`. Do not report that as a GUI workflow test.
