#!/usr/bin/env bash
# Daily job (launchd: ~/Library/LaunchAgents/com.christianuhl.garmin-sync.plist):
# incremental Garmin sync + Apple Notes import, then rebuild the dashboards.
set -uo pipefail
cd "$(dirname "$0")"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') daily run"
# Runs at login/boot too (the Mac is often off for days): wait up to ~10 min for the network.
for i in $(seq 1 20); do
  curl -s -o /dev/null --max-time 10 https://personal.connectapi.garmin.com && break
  echo "network not ready, retry $i/20"; sleep 30
done
./sync.sh
sync_status=$?
if [ -f garmin-dashboards/Makefile ] && grep -q '^all:' garmin-dashboards/Makefile; then
  make -C garmin-dashboards all || echo "dashboard build failed"
  # AI review of the last complete week (only when none exists yet), then rebuild so the Overview shows it
  if garmin-dashboards/.venv/bin/python weekly_review.py --no-build; then
    make -s -C garmin-dashboards all || true
  else
    echo "weekly review failed (will retry on the next run)"
  fi
fi
exit $sync_status
