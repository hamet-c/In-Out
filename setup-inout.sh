#!/usr/bin/env bash
# Sets up In-Out on this computer. Safe to run again; finished steps are skipped.
cd "$(dirname "$0")" || exit 1

failed() {
  echo
  echo "Setup stopped. Fix the problem above, then run this again."
  exit 1
}

# --- Find Python 3.10 or newer ---------------------------------------------
PY=
for candidate in python3 python; do
  if "$candidate" -c "import sys; sys.exit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
    PY=$candidate
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed and wasn't found."
  echo "Install it with the package manager, for example: sudo apt install python3 python3-venv"
  failed
fi

# --- Python environment and components -------------------------------------
# A .venv without pip is left from a failed try, or was copied from Windows: make it again.
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "Creating the Python environment..."
  if ! "$PY" -m venv --clear .venv; then
    echo "On Ubuntu, Debian or Mint, add Python's venv support first: sudo apt install python3-venv"
    failed
  fi
fi
echo "Installing In-Out's components..."
if [ -d vendor ]; then
  # Offline install from a copied "vendor" folder, see README.
  .venv/bin/python -m pip install --disable-pip-version-check -q --no-index -f vendor -r requirements.txt || failed
else
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || failed
fi

# --- Guided setup ------------------------------------------------------------
.venv/bin/python manage.py setup || failed
