#!/usr/bin/env bash
# Re-fetch Garmin data into garmin.db. Pass-through args, e.g.:
#   ./sync.sh            incremental (last synced day minus 3 days .. today, plus new activities)
#   ./sync.sh --full     everything since the first day with data
#   ./sync.sh --days 14  re-fetch the last 14 days
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q fitdecode==0.10.0
fi
exec .venv/bin/python garmin_sync.py "$@"
