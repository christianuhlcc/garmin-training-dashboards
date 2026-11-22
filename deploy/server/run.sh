#!/usr/bin/env bash
# The server's sync job (systemd garmin-sync.service, user garmin). Started by
#   garmin-sync.timer   a few times a day
#   garmin-inbox.path   whenever something lands in the upload inbox (iPhone Shortcut, "Sync now", Mac push)
# systemd never runs two copies of the service at once; uploads that arrive during a run trigger the next one.
#
# Steps: take the inbox -> Garmin sync -> notes import -> review import -> dashboards -> publish site -> backup.
set -uo pipefail
cd "$(dirname "$0")/../.."           # /opt/garmin
source state/env.sh                  # written by setup.sh: INBOX, SITE_OUT, BACKUP_BUCKET, NOTE_TITLE, AWS_DEFAULT_REGION

PY=garmin-dashboards/.venv/bin/python
WORK=state/inbox-work
mkdir -p "$WORK" notes logs
echo "=== $(date '+%Y-%m-%d %H:%M:%S') server run"

# 1. Take the inbox FIRST, before anything can fail: the path unit retriggers as long as the inbox is not empty,
#    so a file left behind by a failing step would loop the job until systemd's trigger limit stops the path unit.
for f in "$INBOX"/*; do
  [ -e "$f" ] || continue
  mv -f "$f" "$WORK/" && echo "inbox: $(basename "$f") ($(wc -c < "$WORK/$(basename "$f")") bytes)"
done
rm -f "$WORK/sync"   # the trigger file carries no data

# 2. Note snapshot: keep the newest upload as notes/<slug>.txt (same place the Mac import keeps it).
note_slug=$(printf '%s' "$NOTE_TITLE" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9_-]\{1,\}/_/g')
if [ -f "$WORK/workout_2026.txt" ]; then
  if [ -s "$WORK/workout_2026.txt" ]; then
    # Two senders (iPhone, Mac): a Mac whose Notes hasn't caught up with iCloud yet may send an older copy.
    # Keep the stored snapshot if the upload's newest session is older than the stored one's.
    if .venv/bin/python - "$WORK/workout_2026.txt" "notes/$note_slug.txt" "$NOTE_TITLE" <<'PY'
import re, sys
from pathlib import Path
import notes_import as n
new, old, title = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
year = int(re.search(r"20\d\d", title).group())
newest = lambda p: max((s["date"] for s in n.parse_note(p.read_text(), year) if s["date"]), default=None)
a, b = newest(new), (newest(old) if old.exists() else None)
print(f"notes: upload newest session {a}, stored {b}")
sys.exit(0 if b is None or (a is not None and a >= b) else 1)
PY
    then
      mv -f "$WORK/workout_2026.txt" "notes/$note_slug.txt"
    else
      echo "notes: upload is older than the stored snapshot, keeping the stored one"
      mv -f "$WORK/workout_2026.txt" "$WORK/workout_2026.older.txt"
    fi
  else
    echo "notes: ignoring empty upload"; rm -f "$WORK/workout_2026.txt"
  fi
fi

# 3. Garmin sync (no Apple Notes on Linux: --skip-notes, else every run counts as failed and the window never advances)
./sync.sh --skip-notes
sync_status=$?

# 4. Notes import after the sync, so new Garmin activities get linked. --title must stay the Apple Note's title:
#    rows are replaced per source title (a different title would duplicate the whole log).
if [ -f "notes/$note_slug.txt" ]; then
  .venv/bin/python notes_import.py --file "notes/$note_slug.txt" --title "$NOTE_TITLE" || sync_status=1
fi

# 5. Weekly review generated on the Mac (weekly_review.py runs only there).
if [ -f "$WORK/review.json" ]; then
  if $PY weekly_review.py --import "$WORK/review.json"; then rm -f "$WORK/review.json"
  else mv -f "$WORK/review.json" "$WORK/review.failed.json"; sync_status=1; fi
fi

# 6. Dashboards, then publish atomically enough for one reader (rsync writes temp files and renames).
if make -s -C garmin-dashboards all; then
  rsync -a --delete --delay-updates garmin-dashboards/build/site/ "$SITE_OUT/"
else
  echo "dashboard build failed"; sync_status=1
fi

# 7. Backup + the Mac's read copy: consistent snapshot (WAL-safe), gzip, S3 (versioned bucket keeps history).
if [ -n "${BACKUP_BUCKET:-}" ]; then
  snap=state/garmin.snapshot.db
  rm -f "$snap" "$snap.gz"
  if .venv/bin/python -c "import sqlite3,sys; s=sqlite3.connect('garmin.db'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close()" "$snap" \
     && gzip -f "$snap" \
     && aws s3 cp --only-show-errors "$snap.gz" "s3://$BACKUP_BUCKET/backup/garmin.db.gz" \
     && aws s3 sync --only-show-errors data/fit "s3://$BACKUP_BUCKET/backup/fit"; then
    echo "backup: s3://$BACKUP_BUCKET/backup/ ok"
  else
    echo "backup failed"; sync_status=1
  fi
  rm -f "$snap" "$snap.gz"
fi

echo "=== done (status $sync_status)"
exit $sync_status
