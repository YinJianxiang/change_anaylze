param(
  [Parameter(Mandatory = $true)][string]$RequirementUrl,
  [Parameter(Mandatory = $true)][string]$Branch,
  [Parameter(Mandatory = $true)][string]$Repo,
  [string]$BaseBranch = "master",
  [string]$RepositoryName = "repository",
  [string]$Output = ".local\change-analysis.json",
  [string]$ReportOutput = "",
  [string]$ReporterDir = "",
  [switch]$NoReport,
  [string]$EnvFile = "",
  [switch]$NoFetch
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
if ([string]::IsNullOrWhiteSpace($ReporterDir)) {
  $ReporterDir = Join-Path (Split-Path -Parent $root) "reporter"
}

$args = @(
  "-m", "change_analysis_harness",
  $RequirementUrl,
  $Branch,
  "--repo", $Repo,
  "--base-branch", $BaseBranch,
  "--repository-name", $RepositoryName,
  "--env-file", $EnvFile,
  "--output", $Output,
  "--reporter-dir", $ReporterDir
)
if ($NoFetch) { $args += "--no-fetch" }
if ($NoReport) { $args += "--no-report" }
if (-not [string]::IsNullOrWhiteSpace($ReportOutput)) { $args += @("--report-output", $ReportOutput) }

Push-Location $root
try {
  & $python @args
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
