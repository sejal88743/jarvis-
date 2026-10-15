@echo off
REM ═══════════════════════════════════════════════════════════════════════════
REM  MARK LV — one-click builder for the one-click installer.
REM
REM  Double-click this file. It checks Python, installs build dependencies,
REM  builds the application, and compiles dist\installer\MARK-LV-*-Setup.exe.
REM
REM  Requirements: Python 3.10+ and Inno Setup 6 (free, jrsoftware.org).
REM  If Inno is missing, the script says so and tells you where to get it.
REM ═══════════════════════════════════════════════════════════════════════════
setlocal
cd /d "%~dp0"

echo.
echo  MARK LV — building the one-click installer
echo  ------------------------------------------
echo.

REM Prefer the py launcher, which always finds a real Python on Windows.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 build_installer.py %*
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python build_installer.py %*
    ) else (
        echo  ERROR: Python was not found.
        echo.
        echo  Install Python 3.10 or newer from https://python.org
        echo  and tick "Add Python to PATH" during setup, then run this again.
        echo.
        pause
        exit /b 1
    )
)

set RESULT=%errorlevel%
echo.
if %RESULT%==0 (
    echo  Done. The setup file is in:  dist\installer\
    start "" "dist\installer"
) else (
    echo  The build did not finish. The reason is printed above.
)

echo.
pause
endlocal
