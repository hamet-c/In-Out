#!/usr/bin/env bash
# Stops In-Out if it's running in the background.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "In-Out isn't set up yet."
  exit 1
fi
.venv/bin/python run.py --stop
status=$?
sleep 3  # time to read the result in the icon's window
exit $status
