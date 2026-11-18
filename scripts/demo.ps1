[CmdletBinding()]
param(
    [switch]$RealTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found: $python"
}

Push-Location $project
try {
    Write-Host '== Help =='
    & $python -m esim_tool_manager --help

    Write-Host "`n== Inventory =="
    & $python -m esim_tool_manager list

    Write-Host "`n== Selected versions =="
    & $python -m esim_tool_manager version ngspice
    & $python -m esim_tool_manager version kicad --system

    Write-Host "`n== Doctor: full inventory (missing optional tools are expected) =="
    & $python -m esim_tool_manager doctor
    $doctorStatus = $LASTEXITCODE

    Write-Host "`n== Doctor: analog simulation (isolated runtime state) =="
    & $python -m esim_tool_manager doctor --flow simulation

    Write-Host "`n== Environment preview =="
    & $python -m esim_tool_manager env --shell powershell

    Write-Host "`n== Isolated child probe =="
    & $python -m esim_tool_manager env --apply --shell powershell

    Write-Host "`n== Runtime verification =="
    & $python -m esim_tool_manager verify ngspice
    $verifyStatus = $LASTEXITCODE

    Write-Host "`n== Toolchain detection, checksum and simulation validation =="
    & $python -m esim_tool_manager validate
    $validateStatus = $LASTEXITCODE

    Write-Host "`n== Dry runs and update checks =="
    & $python -m esim_tool_manager install ngspice --version 47 --dry-run
    $installNgspiceStatus = $LASTEXITCODE
    & $python -m esim_tool_manager updates --tool ngspice
    $updatesNgspiceStatus = $LASTEXITCODE
    & $python -m esim_tool_manager install kicad --dry-run
    $installKicadStatus = $LASTEXITCODE
    & $python -m esim_tool_manager updates --tool kicad
    $updatesKicadStatus = $LASTEXITCODE

    Write-Host "`n== KiCad transfer diagnostics =="
    & $python -m esim_tool_manager diagnose kicad
    $diagnoseStatus = $LASTEXITCODE

    Write-Host "`n== Default test suite =="
    & $python -B -m unittest discover -s tests -v
    $testStatus = $LASTEXITCODE

    if ($RealTests) {
        Write-Host "`n== Real managed-Ngspice integration test =="
        $previous = $env:ESIM_TOOL_MANAGER_REAL_TESTS
        $env:ESIM_TOOL_MANAGER_REAL_TESTS = '1'
        try {
            & $python -B -m unittest tests.test_runtime_features.RealManagedNgspiceTests -v
            $realStatus = $LASTEXITCODE
        }
        finally {
            if ($null -eq $previous) {
                Remove-Item Env:ESIM_TOOL_MANAGER_REAL_TESTS -ErrorAction SilentlyContinue
            }
            else {
                $env:ESIM_TOOL_MANAGER_REAL_TESTS = $previous
            }
        }
    }
    else {
        $realStatus = 0
    }

    Write-Host "`n== Summary =="
    Write-Host "doctor exit (informational): $doctorStatus"
    Write-Host "verify exit: $verifyStatus | validate exit: $validateStatus | tests exit: $testStatus | real tests exit: $realStatus"
    Write-Host "install ngspice: $installNgspiceStatus | updates ngspice: $updatesNgspiceStatus | install kicad: $installKicadStatus | updates kicad: $updatesKicadStatus | diagnose kicad: $diagnoseStatus"
    if ($verifyStatus -ne 0 -or $validateStatus -ne 0 -or $testStatus -ne 0 -or $realStatus -ne 0 -or
        $installNgspiceStatus -ne 0 -or $updatesNgspiceStatus -ne 0 -or $installKicadStatus -ne 0 -or
        $updatesKicadStatus -ne 0 -or $diagnoseStatus -ne 0) {
        exit 1
    }
}
finally {
    Pop-Location
}
