@echo off
rem Double-click to open Vendorus (Windows).
rem First run: sets up Python packages and adds a "Vendorus" shortcut to
rem your desktop. After that, use the shortcut.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo Setting up Vendorus for the first time. This takes a few minutes...
  where py >nul 2>nul && (py -3 launcher.py --setup-only) || (python launcher.py --setup-only)
  if errorlevel 1 (
    echo.
    echo Setup failed. Vendorus needs Python 3.12 or newer from https://www.python.org/downloads/
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" launcher.py --install-shortcut
)

rem Open the app window without a console window.
start "" ".venv\Scripts\pythonw.exe" launcher.py %*
