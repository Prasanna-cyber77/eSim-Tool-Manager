# Testing evidence and limits

Run the suite from the manager workspace:

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest discover -s tests -v
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 -RealTests
```

The archive tests use locally generated fixtures, mocked archive downloads and mocked console version results. They cover checksum failure before extraction, corrupt archives, traversal, unsafe links and Windows device names, interrupted transfer, failed version probes, atomic publication, idempotency and detection of modified support files. Decoder regression tests exercise a solid `.7z`, corrupt input, rejected paths and a fixture created by Windows tar. The production decoder uses `py7zr`. The CLI tests cover approval, failure logs, and diagnostics parsing. Root-discovery tests cover explicit, configured, `ESIM_HOME`, current-directory, sibling and fallback precedence; ambiguous siblings are never guessed. Environment tests cover spaces and apostrophes in paths, idempotence, preservation of existing values, shell dialects, and proof that the parent process environment is unchanged. Receipt tests cover corruption, schema mismatch, wrong tool, modified executables, path and version mismatches, and failure receipts. Portable inspection also covers link detection when Windows-only `stat` attributes are unavailable, guarding the Ubuntu path. Simulation tests cover success, wrong numbers, nonzero exits, simulator error text, timeouts, missing executables, bounded captures, and removal of the temporary workspace. Managed-inspection tests cover modified, missing, extra and unreadable receipt files. The automated suite performs no real software installation.

The default suite passed **102 tests** with **101 passed and one skipped** after the final audit. Per-file counts are 15 CLI/configuration, 16 discovery/health, 19 packages, 17 portable, and 35 runtime features. The skipped test is the real managed-Ngspice integration test. Run it explicitly with:

```powershell
$env:ESIM_TOOL_MANAGER_REAL_TESTS = '1'
& '.\.venv\Scripts\python.exe' -B -m unittest tests.test_runtime_features.RealManagedNgspiceTests -v
Remove-Item Env:ESIM_TOOL_MANAGER_REAL_TESTS
```

The real test passed against the managed Ngspice 47 receipt and binary: receipt/checksums verified, isolated voltage-divider simulation verified, `v(out) = 1.0`, expected `1.0`.

## Runtime verification evidence

The production command and its observed output (the receipt timestamp and duration are refreshed by each run):

```text
> & $python -m esim_tool_manager verify ngspice
ngspice: C:\Users\Prasanna\AppData\Local\eSimToolManager\tools\ngspice\47\bin\ngspice_con.exe
Version: 47 | Source: managed Ngspice archive | Managed receipt and checksums: verified
Simulation: verified | circuit voltage-divider-dc-op-v1 | v(out) = 1.0 (expected 1.0) | exit 0 | 0.283943s
Analog runtime compatibility: verified
Co-simulation/NGHDL compatibility: unverified (separate classification; not exercised).
```

`validate` repeats the discovery, managed receipt, version and simulation checks and reports `Overall: PASS`. The receipt is bound to the executable digest; it is not a generic version-banner claim. A dry run of `install ngspice --version 47 --dry-run` reports `already installed`. `updates --tool ngspice` reports the same reviewed catalog version. `doctor --flow simulation` without an exported `ESIM_NGSPICE` deliberately stays `unverified` for workflow wiring even when the isolated runtime receipt is valid. With `ESIM_NGSPICE` and `SPICE_LIB_DIR` exported for the process, the analog flow reports `ready` and `Compatibility: verified`.

## Read-only checks

- Live WinGet reported no Ngspice match. The KiCad preview selected 10.0.8, Windows x64, machine scope, with the publisher's expected digest.
- The Ngspice archive has **13,814,879 bytes** and matched the catalog SHA-256. The original Windows tar converter reproduced exit `3221225477`, and its listing operation reported unreadable filenames. The corrected decoder successfully processed the identical authentic archive.
- The reference eSim `KicadNetlister.xml_to_spice_lines` converted its existing XML fixture to `r1 in out 1k` using a temporary file outside the reference tree. This checks the parser boundary without KiCad. Live schematic export remains untested.

## Recorded KiCad failures

The manager's former 9.0.3 default matched the bundled eSim KiCad pin. Live WinGet metadata now selects the system version. The native 9.0.3 logs show the manager passed exact ID, source, scope, architecture, version and `--no-upgrade`. The available 10.0.8 failure log shows a direct WinGet invocation with exact ID, source and version.

| KiCad version | Publisher asset bytes | Bytes checked by WinGet | Expected SHA-256 | Recorded actual SHA-256 |
| --- | ---: | ---: | --- | --- |
| 9.0.3 | 1,107,445,456 | 95,420,416 | `582635ced8552c07e043ce9f2eba01dc0c42c96dcb87ce12ba85db1530da593f` | `d38473e22b9079d28457a7215fd2c7d9106ed4710eeb75a0746d4575931d4928` |
| 9.0.3 | 1,107,445,456 | 139,460,608 | same as above | `73f8ac6a8654d52b1d7606bb8bd570939412b33d8180dd2566e78b18cd804144` |
| 10.0.8 | 969,079,176 | 915,406,848 | `5652b9ed52fca4aa63ec2ec61c8bfd4fab4f6a2fbfdb441def2972b0e9b2dfc7` | `5d614c4bc7f7068dddfc01c9251bbf6af316745b4322a24184731f24806df3e0` |

The expected hashes match the publisher asset records. All three files WinGet checked were shorter than the published assets. Delivery Optimization switched to WinINet, and the 10.0.8 log records a timeout before that handoff. The available evidence establishes incomplete files and leaves the underlying cause of the short transfer unresolved. The reported `NotSigned` result for the rejected file does not authenticate its origin. [9.0.3 asset metadata](https://api.github.com/repos/KiCad/kicad-source-mirror/releases/tags/9.0.3), [10.0.8 asset metadata](https://api.github.com/repos/KiCad/kicad-source-mirror/releases/tags/10.0.8)

## Approved installation and runtime validation

The approved per-user installation completed using the checksum-verified archive retained under the workspace's ignored `.validation` directory. `py7zr==1.1.4` and its dependencies were installed only into the existing project `.venv`. The console probe reported `ngspice-47`, the published receipt and file hashes passed verification, and the repeat install preview reported `already installed`.

A temporary analog netlist used a 2 V source and two 1 kOhm resistors. `ngspice_con.exe -b` returned exit 0 and reported the expected 1 V midpoint. This validates the real stock analog simulator. The reference repository remained clean.

## Pending real validation

- Repeat Ngspice archive installation and analog execution in a disposable VM.
- Launch eSim itself with `ESIM_NGSPICE` set and capture the analog workflow through the GUI.
- Successful KiCad installation and live `kicadxml` export on a copied eSim schematic.
- Real `d_cosim`, NGHDL and NgVeri runtime execution against a matching eSim Full installation.
- Real Ubuntu APT integration and private GitHub submission.
