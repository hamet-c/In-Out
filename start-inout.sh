#!/usr/bin/env bash
# Starts In-Out in the background (if it isn't running already) and opens the clock.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "In-Out isn't set up yet. Run ./setup-inout.sh first."
  read -rp "Press Enter to close."
  exit 1
fi
if ! .venv/bin/python run.py --background --open; then
  read -rp "Press Enter to close."  # the icon's window would otherwise vanish with the error
  exit 1
fi
