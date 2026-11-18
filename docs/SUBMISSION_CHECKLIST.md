# Submission checklist

## Code and safety

- [x] Single CLI with `list`, `version`, `doctor`, `env`, `verify`, `validate`, `updates`, `install`, `upgrade`, `config`, `diagnose`.
- [x] Explicit eSim root precedence, sibling discovery, canonical paths, and ambiguity refusal.
- [x] Process-scoped environment preview with PowerShell, cmd, and bash snippets; no permanent PATH or registry mutation.
- [x] Trusted analog fixture with numeric validation, timeout, exit-code, error-text, and temporary-workspace checks.
- [x] Receipts bound to executable path, SHA-256, version, circuit, and observed value.
- [x] Managed archive SHA-256 verification, entry validation, atomic publication, and receipt/file checks.
- [x] WinGet and APT plans rechecked after approval; rejected KiCad installers were never executed.
- [x] No secrets, credentials, personal configuration, or vendor binaries added to source or documentation.
- [x] Reference `eSim` checkout remains read-only.

## Tests

- [x] Default unit/integration/CLI/negative suite passes: **104 run, 103 passed, 1 skipped**.
- [x] Real managed-Ngspice integration runs separately with `ESIM_TOOL_MANAGER_REAL_TESTS=1`: receipt verified and `v(out) = 1.0`.
- [x] Root discovery, environment idempotence, spaces/apostrophes, corrupt receipts, stale receipts, simulation failures, and JSON guard paths covered.
- [x] Windows subprocess argument and timeout behavior exercised with a real child process.
- [ ] Run the suite on an Ubuntu 24.04 or 26.04 VM.
- [ ] Run the full suite in a disposable Windows VM, not only the development host.

## Evidence

- [x] Recorded managed Ngspice 47 receipt verification and file count.
- [x] Recorded `verify ngspice` output and `validate` PASS on the development host.
- [x] Recorded `install ngspice --dry-run` and `updates --tool ngspice` output.
- [x] Recorded KiCad WinGet hash failures and confirmed no rejected installer execution.
- [ ] Capture a confirmed KiCad installation, system version, health report, and live `kicadxml` export.
- [ ] Capture eSim GUI analog launch with `ESIM_NGSPICE` exported.
- [ ] Capture real `d_cosim`, NGHDL, and NgVeri executions with a matching eSim Full tree.
- [ ] Capture approved upgrade evidence on Windows and Ubuntu VMs.

## Documentation

- [x] README with Windows and Ubuntu usage, new commands, scope, and limits.
- [x] Architecture, installation, testing, compatibility, troubleshooting, and implementation-plan documents.
- [x] Reproducible demo script: `scripts/demo.ps1`.
- [x] Overnight progress report with commands, actual outputs, counts, and blockers.
- [x] Final reviewer pass for stale claims, test counts, cross-platform guards, and repository hygiene.
- [x] Local Git repository initialized with a clean initial commit; installers, virtual environments, caches, logs, generated receipts, and local settings are ignored.

## Submission gates requiring approval

- [ ] Approve a disposable-VM Ngspice installation and KiCad installation.
- [ ] Approve an Ubuntu VM APT transaction.
- [x] GitHub repository created and published: https://github.com/Prasanna-cyber77/eSim-Tool-Manager.git.
- [ ] Do not push or publish follow-up commits until explicitly approved.

## Claim boundaries

Reported as verified: Windows 11 x64 CLI behavior, sibling root discovery, managed Ngspice 47 receipt/checksums, isolated standard analog simulation, environment preview without mutation, dry-run/update behavior, and the default test suite.

Reported as unverified: eSim GUI analog workflow, KiCad netlisting and live export, `d_cosim`, NGHDL, NgVeri, installed eSim Full integration, and all Ubuntu runtime behavior. Report rejected KiCad WinGet downloads as blocked, not successful.
