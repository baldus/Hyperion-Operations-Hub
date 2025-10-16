@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ---------------------------------------------------------------------------
rem Windows bootstrapper (pure batch implementation)
rem Installs Python/PostgreSQL via winget, prepares a virtualenv, installs pip
rem dependencies, and emits invapp2/.env.local without requiring PowerShell
rem execution policies to be relaxed.
rem ---------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
if not defined SCRIPT_DIR set "SCRIPT_DIR=.\"
pushd "%SCRIPT_DIR%" >nul
set "REPO_ROOT=%CD%"

call :Main %*
set "EXIT_CODE=%ERRORLEVEL%"
popd >nul
exit /b %EXIT_CODE%

:Main
set "PYTHON_PACKAGE=Python.Python.3.11"
set "POSTGRES_PACKAGE=PostgreSQL.PostgreSQL"
set "VENV_DIR=.venv"
set "SKIP_POSTGRES="
set "SHOW_HELP="
set "ELEVATION_TRIGGERED="

call :ParseArgs %*
if defined SHOW_HELP (
    call :ShowUsage
    exit /b 0
)
call :EnsureAdministrator %*
set "ADMIN_STATUS=%ERRORLEVEL%"
if defined ELEVATION_TRIGGERED (
    exit /b %ADMIN_STATUS%
)
if not "%ADMIN_STATUS%"=="0" (
    exit /b %ADMIN_STATUS%
)

echo [INFO] Working directory: %REPO_ROOT%

call :EnsureWinget || exit /b 1
call :EnsurePython || exit /b 1
if not defined SKIP_POSTGRES (
    call :EnsurePostgres || exit /b 1
) else (
    echo [WARN] Skipping PostgreSQL installation as requested.
)

call :PrepareVirtualEnv || exit /b 1
call :InstallPythonDeps || exit /b 1
call :EnsureEnvFile || exit /b 1

echo.
echo [SUCCESS] Windows bootstrap complete.
echo Next steps:
echo   1. Ensure PostgreSQL is running and matches the connection string in invapp2\.env.local
echo   2. Activate the virtual environment with: "%REPO_ROOT%\%VENV_DIR%\Scripts\activate.bat"
echo   3. Export environment variables (or rename .env.local to .env) before launching Flask/gunicorn.

exit /b 0

:ParseArgs
if "%~1"=="" goto :EOF
if /I "%~1"=="--skip-postgres" (
    set "SKIP_POSTGRES=1"
    shift
    goto :ParseArgs
)
if /I "%~1"=="-skippostgres" (
    set "SKIP_POSTGRES=1"
    shift
    goto :ParseArgs
)
if /I "%~1"=="-h" (
    set "SHOW_HELP=1"
    goto :EOF
)
if /I "%~1"=="/h" (
    set "SHOW_HELP=1"
    goto :EOF
)
if /I "%~1"=="/?" (
    set "SHOW_HELP=1"
    goto :EOF
)
if /I "%~1"=="--help" (
    set "SHOW_HELP=1"
    goto :EOF
)
echo [WARN] Unknown argument "%~1" ignored.
shift
goto :ParseArgs

:ShowUsage
echo Usage: setup_windows.cmd [--skip-postgres]
echo.
echo Installs prerequisites, prepares a virtual environment, and writes invapp2\.env.local.
goto :EOF

:EnsureAdministrator
net session >nul 2>&1
if %errorlevel%==0 goto :EOF

if "%RUN_AS_ADMIN%"=="1" (
    echo [ERROR] Administrative privileges are required to continue.
    exit /b 1
)

echo [INFO] Requesting administrative privileges...
set "ELEVATE_VBS=%TEMP%\hyperion_elevate_%RANDOM%.vbs"
set "ELEVATION_TRIGGERED=1"
>"%ELEVATE_VBS%" echo Set UAC = CreateObject("Shell.Application")
>>"%ELEVATE_VBS%" echo Set Args = WScript.Arguments
>>"%ELEVATE_VBS%" echo Dim Cmd
>>"%ELEVATE_VBS%" echo Cmd = "cd /d ""%REPO_ROOT%"" ^&^& set RUN_AS_ADMIN=1 ^& ""%~f0"""
>>"%ELEVATE_VBS%" echo If Args.Count ^> 0 Then
>>"%ELEVATE_VBS%" echo     Cmd = Cmd ^& JoinArgs(Args)
>>"%ELEVATE_VBS%" echo End If
>>"%ELEVATE_VBS%" echo UAC.ShellExecute "cmd.exe", "/c " ^& Cmd, "", "runas", 1
>>"%ELEVATE_VBS%" echo
>>"%ELEVATE_VBS%" echo Function JoinArgs(arr)
>>"%ELEVATE_VBS%" echo     Dim i, tmp
>>"%ELEVATE_VBS%" echo     tmp = ""
>>"%ELEVATE_VBS%" echo     For i = 0 To arr.Count - 1
>>"%ELEVATE_VBS%" echo         tmp = tmp ^& " " ^& QuoteArg(arr(i))
>>"%ELEVATE_VBS%" echo     Next
>>"%ELEVATE_VBS%" echo     JoinArgs = tmp
>>"%ELEVATE_VBS%" echo End Function
>>"%ELEVATE_VBS%" echo
>>"%ELEVATE_VBS%" echo Function QuoteArg(text)
>>"%ELEVATE_VBS%" echo     Dim safe
>>"%ELEVATE_VBS%" echo     safe = Replace(text, """", """""")
>>"%ELEVATE_VBS%" echo     QuoteArg = Chr(34) ^& safe ^& Chr(34)
>>"%ELEVATE_VBS%" echo End Function

cscript //nologo "%ELEVATE_VBS%" %*
set "ELEVATE_CODE=%ERRORLEVEL%"
del "%ELEVATE_VBS%" >nul 2>&1
if not "%ELEVATE_CODE%"=="0" (
    echo [ERROR] Elevation helper returned exit code %ELEVATE_CODE%.
)
exit /b %ELEVATE_CODE%

:EnsureWinget
where winget >nul 2>nul
if not errorlevel 1 goto :EOF
echo [ERROR] winget was not found. Install App Installer from the Microsoft Store and re-run this script.
exit /b 1

:EnsurePython
call :DetectPython
if defined PYTHON_CMD goto :EOF

echo [INFO] Installing Python via winget...
call :InstallWithWinget "%PYTHON_PACKAGE%" "Python"
if errorlevel 1 exit /b 1

call :DetectPython
if defined PYTHON_CMD goto :EOF

echo [ERROR] Python is still unavailable. Sign out/in or open a fresh Command Prompt, then re-run setup_windows.cmd.
exit /b 1

:DetectPython
set "PYTHON_CMD="
set "PYTHON_LABEL="
for /f "delims=" %%P in ('where python 2^>nul') do (
    set "PYTHON_CMD=python"
    for /f "delims=" %%V in ('python --version 2^>^&1') do set "PYTHON_LABEL=%%V"
    goto :EOF
)
for /f "delims=" %%P in ('where py 2^>nul') do (
    for /f "delims=" %%V in ('py -3 --version 2^>^&1') do set "PYTHON_LABEL=%%V"
    if not defined PYTHON_LABEL (
        for /f "delims=" %%V in ('py --version 2^>^&1') do set "PYTHON_LABEL=%%V"
    )
    if defined PYTHON_LABEL set "PYTHON_CMD=py"
)
if defined PYTHON_CMD (
    echo [INFO] Found %PYTHON_LABEL% via %PYTHON_CMD%.
)
exit /b 0

:EnsurePostgres
where psql >nul 2>nul
if not errorlevel 1 (
    echo [INFO] PostgreSQL client already available.
    exit /b 0
)

echo [INFO] Installing PostgreSQL via winget...
call :InstallWithWinget "%POSTGRES_PACKAGE%" "PostgreSQL"
if errorlevel 1 (
    echo [WARN] Automatic PostgreSQL installation failed. Install it manually and re-run with --skip-postgres if necessary.
    exit /b 0
)

where psql >nul 2>nul
if errorlevel 1 (
    echo [WARN] PostgreSQL is not immediately available. You may need to open a new Command Prompt after installation.
)
exit /b 0

:InstallWithWinget
setlocal EnableDelayedExpansion
set "PACKAGE=%~1"
set "NAME=%~2"

set "FOUND="
for /f "delims=" %%L in ('winget list --id "%PACKAGE%" --exact 2^>nul ^| findstr /I /C:"%PACKAGE%"') do set "FOUND=1"
if defined FOUND (
    echo [INFO] %NAME% already installed (winget).
    endlocal & exit /b 0
)

winget install --id "%PACKAGE%" --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo [ERROR] winget failed while installing %NAME% (exit code !ERRORLEVEL!).
    endlocal & exit /b 1
)
endlocal & exit /b 0

:PrepareVirtualEnv
if not defined PYTHON_CMD (
    echo [ERROR] Python command not detected.
    exit /b 1
)

set "VENV_PATH=%REPO_ROOT%\%VENV_DIR%"
if exist "%VENV_PATH%\Scripts\python.exe" (
    echo [INFO] Virtual environment already exists at %VENV_PATH%.
) else (
    echo [INFO] Creating virtual environment in %VENV_PATH% ...
    if /I "%PYTHON_CMD%"=="py" (
        py -3 -m venv "%VENV_PATH%"
    ) else (
        python -m venv "%VENV_PATH%"
    )
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        exit /b 1
    )
)
exit /b 0

:InstallPythonDeps
set "VENV_PYTHON=%REPO_ROOT%\%VENV_DIR%\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment Python not found at %VENV_PYTHON%.
    exit /b 1
)

echo [INFO] Upgrading pip/setuptools/wheel...
"%VENV_PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [ERROR] pip upgrade failed.
    exit /b 1
)

echo [INFO] Installing application requirements...
"%VENV_PYTHON%" -m pip install -r "%REPO_ROOT%\invapp2\requirements.txt"
if errorlevel 1 (
    echo [ERROR] pip install failed. See the log above for details.
    exit /b 1
)
exit /b 0

:EnsureEnvFile
set "ENV_FILE=%REPO_ROOT%\invapp2\.env.local"
if exist "%ENV_FILE%" (
    echo [INFO] invapp2\.env.local already exists.
    exit /b 0
)

echo [INFO] Creating starter configuration at %ENV_FILE% ...
>"%ENV_FILE%" echo # Rename to .env and adjust values for your deployment.
>>"%ENV_FILE%" echo DB_URL=postgresql+psycopg2://inv:change_me@localhost/invdb
>>"%ENV_FILE%" echo SECRET_KEY=change_me
>>"%ENV_FILE%" echo ADMIN_USER=superuser
>>"%ENV_FILE%" echo ADMIN_PASSWORD=change_me
>>"%ENV_FILE%" echo ZEBRA_PRINTER_HOST=localhost
>>"%ENV_FILE%" echo ZEBRA_PRINTER_PORT=9100
if errorlevel 1 (
    echo [ERROR] Unable to write %ENV_FILE%.
    exit /b 1
)
exit /b 0
