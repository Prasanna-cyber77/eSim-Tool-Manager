# Installation guide

The CLI operates on Python 3.11+ environments. Tested on Python 3.13.2 (Windows 11 x64) and designed for Python 3.11+ across Windows and Linux. The portable backend uses `py7zr==1.1.4` to decode `.7z` into bounded memory buffers before constructing the intermediate tar. Install the project dependency inside `.venv`.

## Preview from PowerShell

```powershell
Set-Location -LiteralPath 'D:\VS Code\FOSSEE\eSim-Tool-Manager'
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt
& $python -m esim_tool_manager install ngspice --version 47 --dry-run
& $python -m esim_tool_manager install kicad --dry-run
& $python -m esim_tool_manager diagnose kicad --version 10.0.8
```

The approved release is `ngspice-47_64.7z`, approximately 13.8 MB. It comes from the upstream SourceForge project. The catalog pins `59225971bd68cdd1199443649aa4615a9e6d684933f205ab49006a3942518f5a`, corroborated by the independently hosted and moderated Chocolatey package that embeds this exact upstream archive. Chocolatey supplies the verification evidence. The manager fetches the original SourceForge archive and uses no Chocolatey install script. [Official Ngspice download](https://ngspice.sourceforge.io/download.html), [independent package verification](https://community.chocolatey.org/packages/ngspice/47.0.0)

## Approved Ngspice validation

Run in a disposable Windows x64 VM after approval. Compare the source and SHA-256 with the preview and type `y` when prompted.

```powershell
& $python -m esim_tool_manager install ngspice --version 47 --apply
& $python -m esim_tool_manager version ngspice
& $python -m esim_tool_manager install ngspice --version 47 --dry-run
& $python -m esim_tool_manager verify ngspice
& $python -m esim_tool_manager validate
```

Expected results are a console version of `47`, an `already installed` repeat preview, a verified managed receipt, and an isolated simulation with `v(out) = 1.0` and exit status 0. `verify` writes `verification.json` under `%LOCALAPPDATA%\eSimToolManager`; the receipt is invalidated automatically if the executable path, content, or reported version changes. For a scoped eSim health check in the same PowerShell session:

```powershell
& $python -m esim_tool_manager env
& $python -m esim_tool_manager env --apply --shell powershell
$env:ESIM_NGSPICE = Join-Path $env:LOCALAPPDATA 'eSimToolManager\tools\ngspice\47\bin\ngspice_con.exe'
& $python -m esim_tool_manager --esim-root '..\eSim' doctor --flow simulation
```

`env --apply` only probes a child process. To configure the current shell for the eSim launch, evaluate the printed snippet (or set `ESIM_NGSPICE` directly as above). No permanent environment or registry change is made. The session override selects stock Ngspice for analog testing. Use the eSim Full simulator for NGHDL/NgVeri workflows. The built-in verification covers only the trusted analog fixture; it does not launch the eSim GUI, exercise `d_cosim`, or prove NGHDL/NgVeri compatibility.

## Failure recovery

- **WinGet integrity failure:** Save the native logs and use `diagnose kicad` to inspect recorded expected and actual hashes. Review the transfer path before another real attempt. [Microsoft WinGet tools](https://learn.microsoft.com/en-us/windows/package-manager/winget/)
- **Portable verification blocked:** The release needs a reviewed independently authenticated SHA-256. Missing verification data prevents download.
- **Portable checksum, extraction or version failure:** The attempted version is unpublished. Review the error and dry-run again before another approved attempt.
- **Windows tar crash `3221225477` (`0xC0000005`):** The original decoder crashed while reading the authentic upstream archive. The current backend uses the project-local `py7zr` dependency. Install `requirements.txt` before retrying with the corrected code.
- **Broken managed installation:** A directory already exists but its receipt or files failed verification. Review the contents before moving the broken directory out of the managed store. The CLI requires a fresh approval to retry an install.
- **Stale simulation receipt:** Run `verify ngspice` again after replacing or upgrading the simulator. A receipt is accepted only for the same executable path, SHA-256, version and circuit identifier. Do not edit `verification.json` to bypass a failed result.
