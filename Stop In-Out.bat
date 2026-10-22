@echo off
rem Stops In-Out if it's running in the background.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo In-Out isn't set up yet.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py --stop
timeout /t 3 >nul
