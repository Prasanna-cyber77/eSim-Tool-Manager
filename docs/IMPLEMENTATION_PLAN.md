# eSim Automated Tool Manager implementation plan

## Delivery phases

1. **Reference inspection.** Trace the Windows launchers, `CosimConfig`, `ToolchainCheck`, `KicadNetlister`, and Ubuntu and Windows installation recipes. Record the eSim revision and verify package identities against official metadata.
2. **Foundation.** Build a dependency-free Python CLI with OS identification, reproducible tool discovery, time-bounded version probes, and structured logs. Use temporary roots and mocked subprocesses for unit tests.
3. **Installation.** Manage system KiCad through WinGet on Windows x64 and APT on Ubuntu 24.04/26.04. Preview one exact package action by default. Require `--apply` and an affirmative interactive confirmation after displaying the command and version. Recheck the plan before applying, then verify package and executable versions independently. Keep eSim-owned components diagnostic-only.
4. **Updates and configuration.** Compare installed and candidate versions using WinGet metadata and `dpkg --compare-versions` as appropriate. Warn on ambiguous data. Use manager-owned configuration with validated paths and explicit approval for replacement. Read eSim's environment and INI as inputs.
5. **Environment health.** Evaluate netlisting, analog simulation, Verilog Verifier, Icarus co-simulation, legacy NgVeri and NGHDL independently. Add provenance, shadowing and capability evidence, including checks that distinguish two ngspice builds with the same version.
6. **Verification and delivery.** Run automated tests and read-only CLI smoke checks on Windows. Document reproducible execution, limits, and a private submission checklist. Use disposable Windows and Ubuntu VMs for an approved real installation and upgrade demo.
7. **Runtime verification and environment.** Add deterministic eSim root discovery with ambiguity refusal, process-scoped environment previews, a trusted isolated analog fixture, numeric and timeout checks, and executable-bound receipts. Keep the runtime claim separate from eSim GUI, `d_cosim`, NGHDL, NgVeri, and Ubuntu execution.

## Requirement-to-test matrix

| Task requirement | Acceptance criteria and test coverage |
| --- | --- |
| 1. Installation, OS compatibility and version control | Detect supported OS and architecture. Use a verified package ID and exact version. Tests exercise WinGet and APT command construction, absent managers, unsupported platforms, unavailable or rejected versions, no mutation for previews and declined prompts, package-manager failure, and post-install verification. VM demo records a real transaction and the resulting executable version. |
| 2. Updates and approved upgrades | Report available KiCad updates, same/newer installed versions, or unknown metadata. Verify that a stale plan, cancelled approval or unsupported target cannot execute the upgrade. Test native APT version comparison, WinGet installed version parsing and pinned or incompatible candidates. |
| 3. Configuration and environment | Simulate launcher PATH prefixes and resolver precedence without modifying the process environment. Test invalid overrides, manager settings, existing configuration replacement approval, INI interpolation and preservation of reference configuration. |
| 4. Dependency feedback | Test separate results for KiCad, analog, Verifier, d_cosim, legacy NgVeri and NGHDL. Fixtures cover a stock shim with the same ngspice banner as a custom build, missing adapter/VPI/libvvp, GHDL backend detection, and missing tools. Checks report failing paths and remediation. |
| 5. CLI with versions, updates and logs | Exercise text and JSON CLI output, meaningful command exit status, unknown versions, a timed-out subprocess, and machine-readable logs. Review Windows paths with spaces and Unicode. |
| 6. Root discovery and environment | Test explicit, configured, `ESIM_HOME`, current-directory, sibling and fallback precedence; reject ambiguous siblings; canonicalize selected roots. Test child-only environment preparation, idempotence, existing-value preservation, spaces/apostrophes, and PowerShell/cmd/bash snippets without parent mutation. |
| 7. Runtime verification | Run one trusted voltage-divider netlist with the selected console simulator. Require exit 0, no error text, numeric `v(out)` within tolerance, bounded capture, timeout handling, and temporary-workspace cleanup. Bind a receipt to path, SHA-256, version, circuit and result; invalidate it on change. Keep eSim GUI, `d_cosim`, NGHDL and NgVeri explicitly unverified until separately exercised. |
| Optional. Cross-platform package managers | Mock both adapters on every test host. On Ubuntu, validate actual binary ownership and package versions on a disposable VM. On Windows, validate WinGet source, scope, version listing and actual install location. |

Reference test patterns include `src/frontEnd/tests/test_launcher_windows.py` for prefix and sentinel handling, `src/maker/tests/test_cosim_config.py` for precedence and the graphical twin, `src/maker/tests/test_toolchain_check.py` for flow-specific gates, and `src/kicadtoNgspice/tests/test_netlist_generation.py` for preservation of `.cir` on export failure. `windows/tests/test_windows_bootstrap.py` and `test_packaging_pins.py` cover generated config and packaging parity. `Ubuntu/tests/test_install.sh` substitutes stubbed install steps. `src/maker/tests/test_dcosim_simulation.py` runs a counter and operating-point checks when the real simulator and Icarus libraries are present.

## Demo and submission gates

1. Run `python -m unittest discover -s tests -v` inside the project environment. Expect 102 tests: 101 pass and the real managed-Ngspice test is skipped unless `ESIM_TOOL_MANAGER_REAL_TESTS=1`. Run `scripts/demo.ps1 -RealTests` to include the real test.
2. Run `python -m esim_tool_manager verify ngspice` and `validate`. Confirm a success receipt with the expected numeric value and record the executable digest/path/version.
3. In a disposable Windows x64 VM, verify source metadata for `KiCad.KiCad` and the approved demo version, then capture a preview, confirmed install, system executable version, and `doctor`. Where an installed eSim is available, demonstrate that its bundled KiCad can remain selected.
4. Repeat the preview and verification with Ubuntu 24.04 or 26.04. For 24.04, record whether the configured APT candidate meets the netlister minimum and the installer target. Use the VM's configured sources.
5. Validate a real eSim Full installation with a minimal analog and d_cosim fixture. Runtime loadability and the custom operating-point behavior require real examples and are separate from capability-file checks.
6. Include the architecture, plan, README, source, tests and relevant test evidence in a private GitHub repository. Exclude the venv, generated logs and local config. Publish and grant reviewer access only after user approval.
