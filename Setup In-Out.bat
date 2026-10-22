@echo off
rem Sets up In-Out on this computer. Safe to run again; finished steps are skipped.
setlocal
cd /d "%~dp0"
title In-Out setup

rem --- Find Python 3.10 or newer ---------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if defined PY %PY% -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 || set "PY="
if not defined PY (
  echo Python 3.10 or newer is needed and wasn't found.
  echo Install it from python.org, and tick "Add python.exe to PATH" in the installer.
  echo Then run this file again.
  goto :failed
)

rem --- Python environment and components -------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment...
  %PY% -m venv .venv || goto :failed
)
echo Installing In-Out's components...
if exist "vendor\" (
  rem Offline install from a copied "vendor" folder, see README.
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --no-index -f vendor -r requirements.txt || goto :failed
) else (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :failed
)

rem --- Guided setup ------------------------------------------------------------
".venv\Scripts\python.exe" manage.py setup || goto :failed
echo.
pause
exit /b 0

:failed
echo.
echo Setup stopped. Fix the problem above, then run this file again.
pause
exit /b 1
