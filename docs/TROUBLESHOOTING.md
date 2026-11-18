# Troubleshooting

## eSim root is "undetected"

The manager accepts a root only when it contains `VERSION` and `src/frontEnd/Application.py`. Discovery checks an explicit `--esim-root`, the saved `esim_root` setting, `ESIM_HOME`, the current directory, unambiguous siblings, and the Windows fallback. Check what was examined:

```powershell
& $python -m esim_tool_manager --json doctor --flow netlisting
```

Set an explicit path, or save a default after reviewing it:

```powershell
& $python -m esim_tool_manager --esim-root 'D:\path\to\eSim' doctor
& $python -m esim_tool_manager config set esim_root 'D:\path\to\eSim'
```

If two sibling roots are valid, the manager refuses to guess. Choose one with `--esim-root` or the saved setting. Pointing at a source checkout is useful for netlist/tool inspection but does not create the bundled Windows `tools` tree.

## `doctor` says analog simulation is unverified

Two separate states are reported:

1. **Runtime receipt**: `verify ngspice` actually ran the built-in circuit and validated `v(out)`.
2. **eSim wiring**: the process that starts eSim must receive `ESIM_NGSPICE`.

A valid receipt with no exported override remains `unverified` because eSim does not search the manager's private store. Preview and apply the child environment, or evaluate the snippet in the launch shell:

```powershell
& $python -m esim_tool_manager env
& $python -m esim_tool_manager env --apply --shell powershell
```

`env --apply` never modifies the parent shell or registry. It probes an isolated child process.

## Verification fails or becomes stale

`verify ngspice` requires exit 0, no simulator error line, and a numeric `v(out)` of 1.0 within `1e-6`. Check the captured command, stdout, stderr, duration and reasons in JSON:

```powershell
& $python -m esim_tool_manager --json verify ngspice
```

A receipt is invalid after the executable path, SHA-256, reported version, or circuit changes. Re-run `verify ngspice` after upgrading or replacing Ngspice. Do not edit `verification.json`; the digest check is intentional.

## Managed Ngspice is "broken"

The managed receipt or one or more installed files no longer match. The inspection lists modified, missing and extra files. Common causes are manual edits, partial copies, an incomplete archive extraction, or a receipt copied from another version.

```powershell
& $python -m esim_tool_manager version ngspice
& $python -m esim_tool_manager install ngspice --dry-run
```

The manager does not silently reuse or replace a broken version. Review the directory, move the broken tree out of the managed store, and use a fresh approved install only after review.

## Ngspice resolution chooses the wrong executable

Precedence is `ESIM_NGSPICE`, eSim's `[NGHDL] NGHDL_HOME`, launcher paths, then the verified managed store. Inspect the winner, shadowed candidates, and searched paths:

```powershell
& $python -m esim_tool_manager list --verbose
& $python -m esim_tool_manager version ngspice --system
& $python -m esim_tool_manager config show
```

A manager override wins for the probe session. Remove or correct an invalid environment/manager override. A graphical executable is rejected for console probing.

## Ngspice installation preview is blocked

- **`verification blocked`**: the reviewed catalog entry has no usable independently authenticated SHA-256. Do not bypass it.
- **`project dependency py7zr unavailable`**: install the project dependency into the existing environment with `& $python -m pip install -r requirements.txt`.
- **`already installed` but files look wrong**: run `version ngspice` and inspect the managed receipt. A broken store is not reported as healthy.
- **Download interruption or checksum mismatch**: the attempted version is not published. Review the error and dry-run again before another approved attempt. The manager never falls back to an unverified archive.

## WinGet KiCad hash failure

A native `2316632081` (`0x8A150011`) means WinGet rejected the installer before execution. Keep verification enabled. Run `diagnose kicad`, compare the expected and recorded digests with the publisher asset, refresh WinGet metadata, and preview a version whose URL and SHA-256 agree. Rejected installers must not be executed manually. The development host has recorded repeated short transfers for 9.0.3 and 10.0.8; that remains an external blocker.

## PowerShell, cmd, or bash snippet problems

`env --shell powershell|cmd|bash` prints the corresponding snippet. Paths with spaces and apostrophes are quoted for the requested dialect. The snippet is a preview; it does not change the current shell. In PowerShell, evaluate the printed lines in that session or set `ESIM_NGSPICE` directly. Use `--json` if another tool will consume the values.

## Tests skip the real integration case

The default suite skips `RealManagedNgspiceTests` so it stays deterministic on hosts without the managed store. Enable it explicitly:

```powershell
$env:ESIM_TOOL_MANAGER_REAL_TESTS = '1'
& '.\.venv\Scripts\python.exe' -B -m unittest tests.test_runtime_features.RealManagedNgspiceTests -v
Remove-Item Env:ESIM_TOOL_MANAGER_REAL_TESTS
```

## Logs and evidence

Operations are logged as JSON Lines next to the settings:

| Platform | Log |
| --- | --- |
| Windows | `%LOCALAPPDATA%\eSimToolManager\operations.jsonl` |
| Ubuntu | `$XDG_STATE_HOME/esim-tool-manager/operations.jsonl` or `~/.local/state/esim-tool-manager/operations.jsonl` |

Logs contain selected versions, statuses, failure categories, and bounded native output. They do not contain secrets.
