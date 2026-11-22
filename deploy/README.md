# Hosting: logbook.uhl-steine-scherben.org

The dashboards run on the shopmate EC2 instance (t4g.small, Amazon Linux 2023, eu-central-1), behind a password,
updated by a systemd timer and by uploads from the iPhone / the Mac. Nothing else runs permanently: shopmate's nginx
serves the static site and accepts the uploads; a oneshot systemd job does the work.

```
iPhone Shortcut ──PUT workout_2026.txt──┐
"Sync now" button ──PUT sync────────────┤   nginx (shopmate's, TLS + basic auth, PUT only to 3 fixed names)
Mac push.py ──PUT review.json + note────┘     │ writes /srv/garmin/upload/inbox/
                                              ▼
                       garmin-inbox.path ──► garmin-sync.service ◄── garmin-sync.timer (07:30, 13:30, 20:30)
                                              │ run.sh: inbox → Garmin sync → notes import → review import
                                              │         → make all → /srv/garmin/site → backup to S3
Mac push.py ◄── s3://logbook-…/backup/garmin.db.gz (read copy for local queries + the weekly review)
```

- **The server owns `garmin.db`.** The Mac's copy is replaced from the server's snapshot on every push; local
  writes to it are lost. Garmin sync runs only on the server.
- **The weekly review runs only on the Mac** (Claude CLI, ~$1/week) and is uploaded as `review.json`.
- **The strength log** reaches the server as plain text: from the iPhone Shortcut (any time) or the Mac (daily).
  Both send the whole note. The server keeps an upload unless its newest session is older than the stored copy's
  (a Mac whose Notes hasn't synced from iCloud yet can't roll back the phone's newer upload). Limitation: an edit to
  an *older* session sent from a stale copy can still be overwritten; the next upload from the phone fixes it.

| Where | What |
|---|---|
| `server/logbook.nginx.conf` | Server block: TLS, basic auth, security headers, static site, PUT-only inbox, rate limits. |
| `server/setup.sh` | Idempotent root setup on the instance (packages, user `garmin`, dirs, venvs, secrets, systemd, cert, nginx snippet). |
| `server/run.sh` | The job. Output: `journalctl -u garmin-sync`. |
| `server/garmin-sync.{service,timer}`, `server/garmin-inbox.path` | systemd units (memory cap 700 MB, hardened). |
| `deploy.sh` | Mac → S3 → SSM Run Command: ship code (and once, `--with-data`), run `setup.sh`. |
| `set-secrets.sh` | Garmin API key + site password hash → SSM. Password in 1Password (item "Logbook") + Keychain (for `push.py`). |
| `mac/push.py`, `mac/com.christianuhl.logbook-push.plist` | Mac's daily job (replaces `com.christianuhl.garmin-sync`). |
| shopmate repo: `deploy/terraform/logbook.tf` | DNS record, private bucket, instance permissions, `/garmin/prod/{DOMAIN,BUCKET}`. |
| shopmate repo: `nginx/nginx.prod.conf.template`, `docker-compose.prod.yml` | `include /etc/nginx/logbook/*.conf` + mounts of `/srv/garmin/*`. |

## Security

- Basic auth over HTTPS only (HTTP redirects, HSTS). User `logbook`, 24 random characters (~143 bits), bcrypt cost 5
  (checked on every request, so cheap; the strength is the password). A missing htpasswd file means 403, never open.
- No upload except `PUT` of exactly `/inbox/workout_2026.txt`, `/inbox/review.json`, `/inbox/sync` (≤ 2 MB, 6/min/IP);
  no GET/DELETE/listing on the inbox. The note text only reaches the parser, never a shell.
- `X-Robots-Tag: noindex`, `Cache-Control: private, no-cache`, `X-Frame-Options: DENY`, `nosniff`.
- 10 req/s per IP across the site (page load ≈ 15 requests). No fail2ban: nginx logs go to Dash0, not to files,
  and online guessing of a 143-bit password is hopeless anyway.
- Instance: no SSH (SSM only), encrypted EBS, IMDSv2. Job runs as `garmin` with `ProtectSystem=strict`.
- Backups: private, versioned, SSE bucket, TLS-only policy; the instance can write but not delete backups.
- The subdomain is public (Certificate Transparency logs, the shopmate repo's nginx comment). That is fine; the data is not.

## First rollout (in this order)

1. **AWS** (shopmate repo, branch `logbook-hosting`): `cd deploy/terraform && terraform apply logbook.tfplan`
   (DNS, bucket, permissions; 11 new resources, nothing changed).
2. **Secrets**: install the 1Password CLI (`brew install --cask 1password-cli`; 1Password → Settings → Developer →
   *Integrate with 1Password CLI*), then `deploy/set-secrets.sh`: creates the password in 1Password (item "Logbook")
   and the Keychain, puts key + hash in SSM.
3. **Shopmate nginx**: merge `logbook-hosting` to shopmate `main` → CI deploys; nginx now includes the (empty) logbook dir.
4. **Stop the Mac's sync, final sync, migrate**:
   ```sh
   launchctl bootout gui/$(id -u)/com.christianuhl.garmin-sync       # stop the Mac's job ...
   rm ~/Library/LaunchAgents/com.christianuhl.garmin-sync.plist      # ... for good (a plain unload returns at login)
   ./sync.sh                                   # last Mac sync (also imports the note)
   deploy/deploy.sh --with-data                # code + garmin.db snapshot + FIT + Strava archive + caches (~0.5 GB)
   ```
   `setup.sh` gets the cert (webroot via the running nginx, no downtime), installs the nginx snippet, starts the first run.
   Don't re-sync from scratch on the server: intraday data older than ~4.5 months exists only in this DB.
5. **Phone**: open https://logbook.uhl-steine-scherben.org (user `logbook`, password from 1Password item "Logbook"),
   save it in the browser, "Add to Home Screen". Build the Shortcut (below).
6. **Mac push**: `cp deploy/mac/com.christianuhl.logbook-push.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.christianuhl.logbook-push.plist`

## Later deploys

```sh
deploy/deploy.sh                  # code changes (does not touch garmin.db / data)
deploy/set-secrets.sh --rotate    # new password → then deploy/deploy.sh, update the Shortcut
aws ssm start-session --target <instance-id>      # shell; then: sudo journalctl -u garmin-sync -n 100
sudo systemctl start garmin-sync                   # run now (on the instance)
garmin-dashboards/.venv/bin/python deploy/mac/push.py --no-review   # pull the server DB to the Mac + push the note
```

New year: the note becomes "Workout 2027" → change `NOTE_TITLE` in `server/setup.sh` and `mac/push.py`, the upload
name `workout_2026.txt` in `server/logbook.nginx.conf`, `server/run.sh`, `mac/push.py`, and the Shortcut.

## iPhone Shortcut "Logbook sync"

Shortcuts app → **+** → name it *Logbook sync*:

1. **Find Notes** → *Filter*: Name *is* `Workout 2026`, *Limit* 1.
2. **Get Text from Input** (Input = Notes) — makes sure the note is sent as plain text, not rich text.
3. **Get Contents of URL**
   - URL `https://logbook.uhl-steine-scherben.org/inbox/workout_2026.txt`
   - Method **PUT**
   - Headers: `Authorization` = `Basic <base64 of logbook:PASSWORD>`
     (on the Mac: `printf 'logbook:%s' "$(security find-generic-password -s logbook -a logbook -w)" | base64`)
   - Request Body **File** → *Text* (from step 2)
4. **Show Notification** "Logbook: note sent, sync running" (optional: **Open URLs** `https://logbook.uhl-steine-scherben.org`).

Run it from the Home Screen (Share → Add to Home Screen), Siri, or automatically: Shortcuts → Automation → **+** →
Time of Day (e.g. 21:00, daily) → *Run Immediately* → Logbook sync. If it fails while the phone is locked, use a
trigger when the phone is in use, e.g. "App → Notes → Is Closed".

The Shortcut contains the password (in the header): don't share it. After `set-secrets.sh --rotate`, replace the header.

First rollout, also check: `deploy.sh` output has no tar xattr warnings; "Sync now" works from the phone's browser
(credentials reused); the Home Screen icon doesn't ask for the password on every launch (else use a Safari bookmark).

First time: compare the upload with the Mac's export (`notes/workout_2026.txt`) — the server log shows the session
and set counts after the import (`journalctl -u garmin-sync | grep notes`).
