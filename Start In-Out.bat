@echo off
rem Starts In-Out in the background (if it isn't running already) and opens the clock.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo In-Out isn't set up yet. Run "Setup In-Out.bat" first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py --background --open
if errorlevel 1 pause
