@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" collector.py %*
    goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 collector.py %*
    goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
    python collector.py %*
    goto finished
)
echo Python 3.10 or newer is required.
:finished
pause
endlocal
