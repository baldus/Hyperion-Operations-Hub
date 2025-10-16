<#!
.SYNOPSIS
    Bootstraps the Hyperion Operations Hub inventory app on Windows.

.DESCRIPTION
    Installs Python and PostgreSQL (if missing) using winget, creates a Python virtual
    environment, installs pip dependencies, and generates a starter environment file.
    Run this script once from an elevated PowerShell prompt on Windows 10/11.
#>

[CmdletBinding()]
param(
    [string]$PythonPackageId = "Python.Python.3.11",
    [string]$PostgresPackageId = "PostgreSQL.PostgreSQL",
    [string]$VenvPath = ".venv",
    [switch]$SkipPostgres
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-IsAdministrator {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Assert-Command {
    param(
        [Parameter(Mandatory = $true)] [string]$Name
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Install-WithWinget {
    param(
        [Parameter(Mandatory = $true)] [string]$PackageId,
        [Parameter(Mandatory = $true)] [string]$DisplayName
    )

    Assert-Command -Name 'winget'

    $listParams = @('--id', $PackageId, '--exact')
    $result = winget list @listParams | Out-String
    if ($LASTEXITCODE -eq 0 -and $result -match $PackageId) {
        Write-Host "✅ $DisplayName already installed (winget)." -ForegroundColor Green
        return
    }

    Write-Host "⬇️ Installing $DisplayName via winget..." -ForegroundColor Cyan
    $installParams = @(
        'install', '--id', $PackageId, '--exact',
        '--accept-package-agreements', '--accept-source-agreements'
    )

    winget @installParams
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install $DisplayName (exit code $LASTEXITCODE)."
    }
}

if (-not (Test-IsAdministrator)) {
    Write-Warning 'It is recommended to run this script from an elevated PowerShell prompt to allow package installation.'
    Write-Warning 'Tip: launch setup_windows.cmd from Command Prompt to automatically request elevation and bypass execution-policy checks.'
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

Write-Host "📁 Working directory: $RepoRoot" -ForegroundColor Yellow

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host '🐍 Python not detected. Installing Python 3 via winget...' -ForegroundColor Cyan
    Install-WithWinget -PackageId $PythonPackageId -DisplayName 'Python'
} else {
    Write-Host "✅ Python already available at $((Get-Command python).Source)" -ForegroundColor Green
}

if (-not $SkipPostgres) {
    if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
        Write-Host '🐘 PostgreSQL client not detected. Attempting installation...' -ForegroundColor Cyan
        try {
            Install-WithWinget -PackageId $PostgresPackageId -DisplayName 'PostgreSQL'
        } catch {
            Write-Warning "Unable to install PostgreSQL automatically. Install it manually from https://www.postgresql.org/download/windows/ and re-run the script with --SkipPostgres if it is already installed. Details: $_"
        }
    } else {
        Write-Host "✅ PostgreSQL client already available." -ForegroundColor Green
    }
} else {
    Write-Host '⏭️ Skipping PostgreSQL installation as requested.' -ForegroundColor Yellow
}

Assert-Command -Name python

$pythonVersion = (& python --version).Trim()
Write-Host "🐍 Using $pythonVersion" -ForegroundColor Green

$venvFullPath = Join-Path $RepoRoot $VenvPath
if (Test-Path $venvFullPath) {
    Write-Host "ℹ️ Virtual environment already exists at $venvFullPath" -ForegroundColor Yellow
} else {
    Write-Host "📦 Creating virtual environment at $venvFullPath" -ForegroundColor Cyan
    & python -m venv $VenvPath
}

$activateScript = Join-Path $venvFullPath 'Scripts'
$activateScript = Join-Path $activateScript 'Activate.ps1'
if (-not (Test-Path $activateScript)) {
    throw "Virtual environment activation script not found at $activateScript"
}

Write-Host '⬆️ Upgrading pip, setuptools, and wheel...' -ForegroundColor Cyan
. $activateScript
python -m pip install --upgrade pip setuptools wheel

Write-Host '📥 Installing Python dependencies from invapp2/requirements.txt...' -ForegroundColor Cyan
python -m pip install -r (Join-Path $RepoRoot 'invapp2' 'requirements.txt')

$envFile = Join-Path $RepoRoot 'invapp2' '.env.local'
if (-not (Test-Path $envFile)) {
    Write-Host "📝 Generating default environment file at $envFile" -ForegroundColor Cyan
    @"
# Rename to .env and adjust values for your deployment.
DB_URL=postgresql+psycopg2://inv:change_me@localhost/invdb
SECRET_KEY=change_me
ADMIN_USER=superuser
ADMIN_PASSWORD=change_me
ZEBRA_PRINTER_HOST=localhost
ZEBRA_PRINTER_PORT=9100
"@ | Set-Content -Path $envFile -Encoding UTF8
} else {
    Write-Host "ℹ️ Environment file already exists at $envFile" -ForegroundColor Yellow
}

Write-Host "✅ Windows bootstrap complete." -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Start a PostgreSQL server and create the database referenced in invapp2/.env.local." -ForegroundColor Yellow
Write-Host "2. Activate the virtual environment: `& $activateScript`" -ForegroundColor Yellow
Write-Host "3. Export/Set environment variables from .env.local and run 'python app.py' or 'flask run'." -ForegroundColor Yellow
