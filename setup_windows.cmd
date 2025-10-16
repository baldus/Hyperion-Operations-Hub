@echo off
setlocal enabledelayedexpansion

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

REM Attempt to remove the "downloaded from the internet" marker so ExecutionPolicy checks
REM do not immediately block the bootstrapper on fresh downloads.
"%POWERSHELL%" -NoProfile -Command "try { Unblock-File -Path '%PS1%' -ErrorAction Stop } catch { }" >nul 2>nul

REM Ensure we are running with administrative privileges (required for winget installs).
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Requesting administrative privileges (required for package installation)...
    "%POWERSHELL%" -NoProfile -Command "
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File """%PS1%""" %*';
        try {
            $process = Start-Process -FilePath '%POWERSHELL%' -ArgumentList $arguments -Verb RunAs -Wait -PassThru
            exit $process.ExitCode
        } catch [System.ComponentModel.Win32Exception] {
            if ($_.NativeErrorCode -eq 1223) {
                Write-Error 'User declined the elevation prompt.'
                exit 1223
            }
            throw
        }
    "
    set "EXIT_CODE=%ERRORLEVEL%"
    if "!EXIT_CODE!"=="1223" (
        echo [WARN] The elevation prompt was declined. Re-run this script and accept the UAC prompt to continue.
    ) else if not "!EXIT_CODE!"=="0" (
        echo [ERROR] Elevated execution failed with exit code !EXIT_CODE!.
        call :ExecutionPolicyHelp
    )
    endlocal & exit /b !EXIT_CODE!
)

echo [INFO] Running bootstrap with administrative privileges.
"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Bootstrap failed with exit code %EXIT_CODE%.
    call :ExecutionPolicyHelp
)
endlocal & exit /b %EXIT_CODE%

:ExecutionPolicyHelp
echo.
echo If you encountered an execution policy error:
echo   1^> Right-click "%PS1%", open Properties, and select ^<Unblock^>.
echo   2^> Or run: powershell -ExecutionPolicy Bypass -File "%PS1%" [optional script arguments]
echo   3^> Or, from within PowerShell, run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
exit /b 0
