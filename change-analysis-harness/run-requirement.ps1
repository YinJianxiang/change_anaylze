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
if (-not (Test-Path $python)) {
  throw "Python virtual environment not found: $python"
}

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
