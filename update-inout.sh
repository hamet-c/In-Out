#!/usr/bin/env bash
# Updates In-Out to the latest version on GitHub. The database is backed up first and kept.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "In-Out isn't set up yet. Run ./setup-inout.sh first."
  exit 1
fi
# The update may replace this very file, so the shell hands over to Python and reads no further.
exec .venv/bin/python manage.py update
