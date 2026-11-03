param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Apk,
    [string]$Output,
    [int]$VersionCode = 0,
    [string]$BuildTools,
    [switch]$Refresh,
    [switch]$KeepIntermediates
)
$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = (Get-Command python -ErrorAction Stop).Source
}
$buildArgs = @((Join-Path $PSScriptRoot 'build.py'), $Apk)
if ($Output) { $buildArgs += @('--output', $Output) }
if ($VersionCode -gt 0) { $buildArgs += @('--version-code', [string]$VersionCode) }
if ($BuildTools) { $buildArgs += @('--build-tools', $BuildTools) }
if ($Refresh) { $buildArgs += '--refresh' }
if ($KeepIntermediates) { $buildArgs += '--keep-intermediates' }
& $pythonExe @buildArgs
exit $LASTEXITCODE
