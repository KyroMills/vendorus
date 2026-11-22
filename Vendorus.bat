@echo off
rem Double-click to start the Vendorus dashboard (Windows).
rem Close this window, or press Ctrl+C, to stop it.
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)

%PY% launcher.py %*
if errorlevel 1 pause
