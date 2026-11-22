#!/usr/bin/env python3
"""Mac side of the logbook (launchd, daily + at login): the server is the source of truth, the Mac only pushes.

    garmin-dashboards/.venv/bin/python deploy/mac/push.py            # pull DB copy, push note, weekly review if due
    garmin-dashboards/.venv/bin/python deploy/mac/push.py --no-review
    garmin-dashboards/.venv/bin/python deploy/mac/push.py --no-pull  # keep the local garmin.db as it is

1. Pull: the server's nightly snapshot (S3, backup/garmin.db.gz + data/fit) replaces the local garmin.db, so local
   queries, the local dashboards and the weekly brief see the server's data. Local writes to garmin.db are discarded.
2. Note: the Apple Note "Workout 2026" (read via osascript) -> PUT https://<domain>/inbox/workout_2026.txt.
3. Review: weekly_review.py (Claude CLI, ~$1) for the last complete week if it has none yet -> PUT /inbox/review.json.
Each upload makes the server sync + rebuild (systemd path unit). The site password comes from the Keychain
(deploy/set-secrets.sh), the bucket from SSM (/garmin/prod/BUCKET), via the AWS CLI.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import notes_import  # noqa: E402

NOTE_TITLE = "Workout 2026"
DOMAIN = "logbook.uhl-steine-scherben.org"
NOTE_UPLOAD = "workout_2026.txt"          # fixed name, allowed by the server's nginx config
DASH_PY = ROOT / "garmin-dashboards" / ".venv" / "bin" / "python"
REVIEWS = ROOT / "data" / "weekly_reviews"
ENV = dict(os.environ, AWS_DEFAULT_REGION=os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
           PATH=os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] push: {msg}", flush=True)


def run(*cmd: str, **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, env=ENV, **kw).stdout.strip()


def ssm(name: str) -> str:
    return run("aws", "ssm", "get-parameter", "--name", name, "--query", "Parameter.Value", "--output", "text")


def wait_for_network(url: str, tries: int = 20) -> None:
    """launchd also runs this at login/boot, before Wi-Fi is up."""
    for i in range(tries):
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10)
            return
        except urllib.error.HTTPError:
            return                       # 401 = reachable
        except Exception:
            log(f"network not ready, retry {i + 1}/{tries}")
            time.sleep(30)
    raise SystemExit("no network")


def put(base: str, auth: str, name: str, body: bytes) -> None:
    req = urllib.request.Request(f"{base}/inbox/{name}", data=body, method="PUT",
                                 headers={"Authorization": auth, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=60) as r:
        log(f"uploaded {name} ({len(body)} bytes): HTTP {r.status}")


def pull(bucket: str) -> None:
    tmp = ROOT / "garmin.db.pull"
    gz = ROOT / "garmin.db.pull.gz"
    run("aws", "s3", "cp", "--only-show-errors", f"s3://{bucket}/backup/garmin.db.gz", str(gz))
    with gzip.open(gz) as src, open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    gz.unlink()
    for side in ("garmin.db-wal", "garmin.db-shm"):   # a stale WAL would be replayed onto the new file
        (ROOT / side).unlink(missing_ok=True)
    os.replace(tmp, ROOT / "garmin.db")
    run("aws", "s3", "sync", "--only-show-errors", f"s3://{bucket}/backup/fit", str(ROOT / "data" / "fit"))
    log("pulled server snapshot -> garmin.db, data/fit")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-pull", action="store_true", help="don't replace the local garmin.db with the server snapshot")
    ap.add_argument("--no-review", action="store_true", help="don't generate/push the weekly review")
    args = ap.parse_args()

    wait_for_network(f"https://{DOMAIN}")      # first: at login/boot Wi-Fi may not be up yet (AWS calls would fail)
    bucket = ssm("/garmin/prod/BUCKET")
    base = f"https://{DOMAIN}"
    pw = run("security", "find-generic-password", "-s", "logbook", "-a", "logbook", "-w")
    auth = "Basic " + base64.b64encode(f"logbook:{pw}".encode()).decode()
    status = 0

    if not args.no_pull:
        pull(bucket)

    # Review before the note: both uploads trigger a server run; the review needs no fresh note.
    if not args.no_review:
        started = time.time()
        res = subprocess.run([str(DASH_PY), "weekly_review.py"], cwd=ROOT, env=ENV, text=True, capture_output=True)
        print(res.stdout, end="", file=sys.stdout)
        print(res.stderr, end="", file=sys.stderr)
        new = [p for p in REVIEWS.glob("*.json") if p.stat().st_mtime >= started]
        if res.returncode != 0:
            log("weekly review failed (retried next run)"); status = 1
        for p in sorted(new)[-1:]:
            put(base, auth, "review.json", p.read_bytes())

    notes = [(t, txt) for t, txt in notes_import.read_apple_notes(NOTE_TITLE.lower()) if t == NOTE_TITLE]
    if not notes:
        log(f"no Apple Note titled '{NOTE_TITLE}'"); return 1
    text = notes[0][1]
    notes_import.NOTES_DIR.mkdir(exist_ok=True)
    (notes_import.NOTES_DIR / NOTE_UPLOAD).write_text(text)
    put(base, auth, NOTE_UPLOAD, text.encode())

    # Local dashboards from the pulled data (open garmin-dashboards/build/site/index.html offline).
    subprocess.run(["make", "-s", "-C", str(ROOT / "garmin-dashboards"), "all"], env=ENV, capture_output=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
