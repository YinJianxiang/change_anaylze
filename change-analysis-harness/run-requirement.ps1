param(
  [Parameter(Mandatory = $true)][string]$RequirementUrl,
  [string]$Output = ".local\requirement-document.json",
  [string]$EnvFile = ""
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "..\.venv\Scripts\python.exe"
$defaultEnvFile = Join-Path $root ".env"
if (-not (Test-Path $defaultEnvFile)) {
  $defaultEnvFile = Join-Path $root "..\.env"
}
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
  $EnvFile = $defaultEnvFile
}
$projectRoot = Split-Path -Parent $root
if (-not (Test-Path $python)) {
  throw "Python virtual environment not found: $python`nFrom '$projectRoot', run:`n  python -m venv .venv`n  ./.venv/Scripts/python.exe -m pip install -e ./change-analysis-harness"
}
& $python -c "import change_analysis_harness" 2>$null
if ($LASTEXITCODE -ne 0) {
  throw "Harness is not installed in the root virtual environment. From '$projectRoot', run:`n  ./.venv/Scripts/python.exe -m pip install -e ./change-analysis-harness"
}
$EnvFile = [System.IO.Path]::GetFullPath($EnvFile)
$Output = [System.IO.Path]::GetFullPath($Output)

$args = @(
  "-m", "change_analysis_harness.requirement_cli",
  $RequirementUrl,
  "--env-file", $EnvFile,
  "--output", $Output
)

Push-Location $root
try {
  & $python @args
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
