@echo off
rem Updates In-Out to the latest version on GitHub. The database is backed up first and kept.
cd /d "%~dp0"
title Updating In-Out
if not exist ".venv\Scripts\python.exe" (
  echo In-Out isn't set up yet. Run "Setup In-Out.bat" first.
  pause
  exit /b 1
)
rem The update may replace this very file, and cmd re-reads batch files as it goes, so
rem everything that runs after the update must be on this one line, which cmd reads in full first.
".venv\Scripts\python.exe" manage.py update & pause & exit /b
