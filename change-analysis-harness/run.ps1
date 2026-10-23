param(
  [string]$RequirementUrl = "",
  [string]$Branch = "",
  [string]$Repo = "",
  [string]$BaseBranch = "",
  [string]$RepositoryName = "repository",
  [string]$Output = ".local\change-analysis.json",
  [string]$ReportOutput = "",
  [string]$ReporterDir = "",
  [switch]$NoReport,
  [string]$EnvFile = "",
  [string]$DiffFile = "",
  [string[]]$RiskExclude = @(),
  [ValidateSet("low", "medium", "high")][string]$FailOn
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
$EnvFile = [System.IO.Path]::GetFullPath($EnvFile)
if (-not [string]::IsNullOrWhiteSpace($DiffFile)) {
  $DiffFile = [System.IO.Path]::GetFullPath($DiffFile)
  if ($RequirementUrl -or $Branch -or $Repo) { throw "DiffFile cannot be combined with RequirementUrl, Branch or Repo" }
} elseif (-not $RequirementUrl -or -not $Branch) {
  throw "RequirementUrl and Branch are required unless DiffFile is used"
}
if (-not [string]::IsNullOrWhiteSpace($Repo)) {
  $Repo = [System.IO.Path]::GetFullPath($Repo)
}
$projectRoot = Split-Path -Parent $root
if (-not (Test-Path $python)) {
  throw "Python virtual environment not found: $python`nFrom '$projectRoot', run:`n  python -m venv .venv`n  ./.venv/Scripts/python.exe -m pip install -e ./change-analysis-harness"
}
& $python -c "import change_analysis_harness" 2>$null
if ($LASTEXITCODE -ne 0) {
  throw "Harness is not installed in the root virtual environment. From '$projectRoot', run:`n  ./.venv/Scripts/python.exe -m pip install -e ./change-analysis-harness"
}
$Output = [System.IO.Path]::GetFullPath($Output)
if ($ReportOutput) { $ReportOutput = [System.IO.Path]::GetFullPath($ReportOutput) }
if ($ReporterDir) { $ReporterDir = [System.IO.Path]::GetFullPath($ReporterDir) }
if ([string]::IsNullOrWhiteSpace($ReporterDir)) {
  $ReporterDir = Join-Path (Split-Path -Parent $root) "reporter"
}

$args = @(
  "-m", "change_analysis_harness",
  "--repository-name", $RepositoryName,
  "--env-file", $EnvFile,
  "--output", $Output,
  "--reporter-dir", $ReporterDir
)
if ($DiffFile) { $args += @("--diff-file", $DiffFile) }
else { $args += @($RequirementUrl, $Branch) }
if ($RiskExclude.Count) { $args += "--risk-exclude"; $args += $RiskExclude }
if ($FailOn) { $args += @("--fail-on", $FailOn) }
if (-not [string]::IsNullOrWhiteSpace($Repo)) { $args += @("--repo", $Repo) }
if (-not [string]::IsNullOrWhiteSpace($BaseBranch)) { $args += @("--base-branch", $BaseBranch) }
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
