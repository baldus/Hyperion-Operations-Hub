$ErrorActionPreference = 'Stop'

# Allow operators to override these locations when deploying to a new host.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = if ($env:APP_DIR) { $env:APP_DIR } else { Join-Path $scriptDir 'invapp2' }
$venvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $appDir '.venv' }
$requirementsFile = if ($env:REQUIREMENTS_FILE) { $env:REQUIREMENTS_FILE } else { Join-Path $appDir 'requirements.txt' }
$appModule = if ($env:APP_MODULE) { $env:APP_MODULE } else { 'app:app' }

if (-not (Test-Path -Path $appDir -PathType Container)) {
    throw "Unable to locate application directory: $appDir"
}

Set-Location $appDir

if (-not (Test-Path -Path $venvDir -PathType Container)) {
    Write-Host "🔹 Creating virtual environment at $venvDir"
    & python -m venv $venvDir | Out-Null
}

$activateScript = Join-Path (Join-Path $venvDir 'Scripts') 'Activate.ps1'
if (-not (Test-Path -Path $activateScript -PathType Leaf)) {
    throw "Unable to locate activation script at $activateScript"
}

Write-Host "🔹 Activating virtual environment"
. $activateScript

Write-Host "🔹 Ensuring tooling is up to date"
& python -m pip install --upgrade pip setuptools wheel | Out-Null

if (Test-Path -Path $requirementsFile -PathType Leaf) {
    $resolvedRequirements = Resolve-Path $requirementsFile
    Write-Host "🔹 Installing Python dependencies from $resolvedRequirements"
    & python -m pip install -r $resolvedRequirements
}
else {
    Write-Warning "Requirements file not found at $requirementsFile — skipping dependency install"
}

if (-not $env:DB_URL) {
    $env:DB_URL = 'postgresql+psycopg2://inv:change_me@localhost/invdb'
    Write-Warning "DB_URL not found; defaulting to $($env:DB_URL)"
}
else {
    Write-Host "✅ Using DB_URL=$($env:DB_URL)"
}

$host = if ($env:HOST) { $env:HOST } else { '0.0.0.0' }
$port = if ($env:PORT) { $env:PORT } else { '8000' }
$threads = if ($env:WAITRESS_THREADS) { $env:WAITRESS_THREADS } else { '4' }

Write-Host "🔹 Starting Hyperion Operations Console via Waitress ($host`:$port)"

$waitressArgs = @("--listen=$host`:$port", "--threads=$threads", $appModule)

& waitress-serve @waitressArgs
