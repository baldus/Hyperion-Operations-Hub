@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%setup_windows.ps1"
if not exist "%PS1%" (
    echo [ERROR] Unable to locate setup_windows.ps1 next to this script.
    exit /b 1
)
set "POWERSHELL=powershell.exe"
where %POWERSHELL% >nul 2>nul
if errorlevel 1 (
    echo [INFO] powershell.exe not found, attempting to use pwsh.exe...
    set "POWERSHELL=pwsh.exe"
    where %POWERSHELL% >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Neither powershell.exe nor pwsh.exe was found in PATH.
        exit /b 1
    )
)
"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
