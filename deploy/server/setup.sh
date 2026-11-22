#!/usr/bin/env bash
# Server setup for the logbook, run as root ON the shopmate EC2 instance (Amazon Linux 2023, arm64).
# Idempotent: deploy/deploy.sh runs it after every code deploy (via SSM Run Command, no SSH).
#
#   setup.sh                         packages, user, dirs, venvs, secrets, systemd, cert, nginx snippet
#   setup.sh --data s3://…/x.tgz     additionally restore the one-time data bundle (refuses to overwrite garmin.db)
#
# Layout:
#   /opt/garmin             code + garmin.db + data/ + venvs (user garmin, 0750)
#   /opt/garmin/state       env.sh (non-secret config), inbox-work/, temp snapshots
#   /srv/garmin/site        published static site (read by nginx)
#   /srv/garmin/nginx       logbook.conf + htpasswd (included by shopmate's nginx)
#   /srv/garmin/upload      inbox/ + tmp/ (written by nginx uid 101, read by group garmin)
#
# Parameters (SSM, region of the instance):
#   /garmin/prod/DOMAIN, /garmin/prod/BUCKET              Terraform (shopmate repo, deploy/terraform/logbook.tf)
#   /garmin/prod/GARMIN_API_KEY, /garmin/prod/HTPASSWD    SecureString, deploy/set-secrets.sh
#   /shopmate/prod/CERTBOT_EMAIL                          shared with shopmate
set -euo pipefail

DATA_BUNDLE=""
FORCE_DATA=false
while [ $# -gt 0 ]; do
  case "$1" in
    --data) DATA_BUNDLE="$2"; shift 2 ;;
    --force-data) FORCE_DATA=true; shift ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
done

APP=/opt/garmin
SRV=/srv/garmin
NGINX_UID=101          # nginx user inside nginx:*-alpine
NOTE_TITLE="Workout 2026"

IMDS_TOKEN=$(curl -sS -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
export AWS_DEFAULT_REGION=$(curl -sS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
param() { aws ssm get-parameter --name "$1" --with-decryption --query Parameter.Value --output text; }

# Every parameter up front: set -e aborts on a failed $(…) assignment (not inside heredocs).
DOMAIN=$(param /garmin/prod/DOMAIN)
BUCKET=$(param /garmin/prod/BUCKET)
CERTBOT_EMAIL=$(param /shopmate/prod/CERTBOT_EMAIL)
GARMIN_API_KEY=$(param /garmin/prod/GARMIN_API_KEY)
HTPASSWD=$(param /garmin/prod/HTPASSWD)

echo "--- packages"
PY=""
for v in 3.12 3.11; do
  if command -v "python$v" >/dev/null || dnf install -y -q "python$v" "python$v-pip" >/dev/null 2>&1; then PY="python$v"; break; fi
done
[ -n "$PY" ] || { echo "no python3.11/3.12 available"; exit 1; }
dnf install -y -q make rsync gzip >/dev/null
echo "python: $($PY --version)"

echo "--- user + dirs"
id garmin >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /sbin/nologin garmin
install -d -o garmin -g garmin -m 0750 "$APP" "$APP/state" "$APP/state/inbox-work" "$APP/logs" "$APP/notes" "$APP/data"
install -d -o root -g root -m 0755 "$SRV" "$SRV/nginx"
install -d -o garmin -g garmin -m 0755 "$SRV/site"
# setgid: files nginx writes get group garmin; dav_access makes them 0660.
install -d -o "$NGINX_UID" -g garmin -m 2770 "$SRV/upload" "$SRV/upload/inbox" "$SRV/upload/tmp"

if [ -n "$DATA_BUNDLE" ]; then
  echo "--- data bundle"
  if [ -e "$APP/garmin.db" ] && [ "$FORCE_DATA" != true ]; then
    echo "refusing: $APP/garmin.db exists (the server is the source of truth now; pass --force-data to overwrite)"; exit 1
  fi
  aws s3 cp --only-show-errors "$DATA_BUNDLE" /tmp/garmin-data.tgz
  tar -xzf /tmp/garmin-data.tgz -C "$APP"
  rm -f /tmp/garmin-data.tgz
  aws s3 rm --only-show-errors "$DATA_BUNDLE"   # one-time transfer; the nightly backup takes over
fi
chown -R garmin:garmin "$APP"

echo "--- config + secrets"
umask 077
cat > "$APP/state/env.sh" <<EOF
# written by deploy/server/setup.sh - do not edit (rewritten on every deploy)
INBOX=$SRV/upload/inbox
SITE_OUT=$SRV/site
BACKUP_BUCKET=$BUCKET
NOTE_TITLE="$NOTE_TITLE"
export AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION
EOF
printf 'GARMIN_API_KEY=%s\n' "$GARMIN_API_KEY" > "$APP/.env"
chown garmin:garmin "$APP/state/env.sh" "$APP/.env"
printf '%s\n' "$HTPASSWD" > "$SRV/nginx/htpasswd"
chown "$NGINX_UID":root "$SRV/nginx/htpasswd"; chmod 0400 "$SRV/nginx/htpasswd"
umask 022

echo "--- venvs"
as_garmin() { runuser -u garmin -- "$@"; }
[ -x "$APP/.venv/bin/python" ] || as_garmin "$PY" -m venv "$APP/.venv"
as_garmin "$APP/.venv/bin/pip" install -q --disable-pip-version-check fitdecode==0.10.0
[ -x "$APP/garmin-dashboards/.venv/bin/python" ] || as_garmin "$PY" -m venv "$APP/garmin-dashboards/.venv"
as_garmin "$APP/garmin-dashboards/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/garmin-dashboards/requirements.txt"

echo "--- systemd"
install -m 0644 "$APP"/deploy/server/garmin-sync.{service,timer} "$APP"/deploy/server/garmin-inbox.path /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now garmin-sync.timer garmin-inbox.path

echo "--- certificate + nginx snippet"
NGINX=$(docker ps -q --filter label=com.docker.compose.project=shopmate --filter label=com.docker.compose.service=nginx)
if [ -z "$NGINX" ]; then
  echo "shopmate nginx is not running - skipping the web part (rerun setup after the shopmate deploy)"; exit 0
fi
if ! docker exec "$NGINX" test -d /etc/nginx/logbook; then
  echo "shopmate nginx has no /etc/nginx/logbook mount yet - deploy shopmate with the logbook include first"; exit 0
fi
if ! docker run --rm -v shopmate_letsencrypt:/etc/letsencrypt alpine test -e "/etc/letsencrypt/live/$DOMAIN/fullchain.pem"; then
  # Webroot through the running nginx: shopmate's :80 server answers ACME challenges for any host name,
  # so no downtime. The certbot sidecar renews this cert together with shopmate's.
  docker run --rm -v shopmate_letsencrypt:/etc/letsencrypt -v shopmate_certbot-webroot:/var/www/certbot certbot/certbot \
    certonly --webroot -w /var/www/certbot -d "$DOMAIN" -m "$CERTBOT_EMAIL" --agree-tos --no-eff-email --non-interactive
fi
sed "s/@DOMAIN@/$DOMAIN/g" "$APP/deploy/server/logbook.nginx.conf" > "$SRV/nginx/logbook.conf.new"
mv -f "$SRV/nginx/logbook.conf.new" "$SRV/nginx/logbook.conf"
if docker exec "$NGINX" nginx -t -q; then
  docker exec "$NGINX" nginx -s reload
  echo "logbook live: https://$DOMAIN"
else
  # Never leave a config behind that would stop shopmate's nginx on its next restart.
  rm -f "$SRV/nginx/logbook.conf"
  echo "nginx rejected logbook.conf - removed it, shopmate unaffected"; exit 1
fi

# First build if the site is still empty (normally the timer / inbox does this).
if [ ! -e "$SRV/site/index.html" ] && [ -e "$APP/garmin.db" ]; then
  echo "--- first run"
  systemctl start --no-block garmin-sync.service
fi
