#!/usr/bin/env bash
# Write the logbook's secrets to SSM Parameter Store (SecureString, read by deploy/server/setup.sh).
#
#   deploy/set-secrets.sh            GARMIN_API_KEY from ./.env; site password from the Keychain (created if missing)
#   deploy/set-secrets.sh --rotate   new random site password (update the iPhone Shortcut afterwards)
#
# The password lives only in the macOS Keychain (service "logbook", account "logbook") and, as a bcrypt hash, in SSM.
# It is never printed. To see it (for the Shortcut / your password manager):
#   security find-generic-password -s logbook -a logbook -w
# Run deploy/deploy.sh afterwards so the server picks the values up.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-eu-central-1}
USER_NAME=logbook

key=$(sed -n 's/^GARMIN_API_KEY=//p' .env | tr -d "\"'")
[ -n "$key" ] || { echo "GARMIN_API_KEY missing in .env"; exit 1; }
aws ssm put-parameter --name /garmin/prod/GARMIN_API_KEY --type SecureString --overwrite --value "$key" >/dev/null
echo "GARMIN_API_KEY -> /garmin/prod/GARMIN_API_KEY"

pw=$(security find-generic-password -s logbook -a "$USER_NAME" -w 2>/dev/null || true)
if [ -z "$pw" ] || [ "${1:-}" = "--rotate" ]; then
  # 24 chars from [A-Za-z0-9]: ~143 bits. Long random beats a slow hash; bcrypt cost 5 keeps per-request checks cheap
  # (basic auth verifies on every request, ~15 per page load).
  pw=$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(24)))')
  security add-generic-password -U -s logbook -a "$USER_NAME" -l "Logbook site (logbook.uhl-steine-scherben.org)" -w "$pw"
  echo "new site password stored in the Keychain (service 'logbook')"
fi
hash=$(printf '%s' "$pw" | htpasswd -niB -C 5 "$USER_NAME" | head -1)
aws ssm put-parameter --name /garmin/prod/HTPASSWD --type SecureString --overwrite --value "$hash" >/dev/null
echo "htpasswd (bcrypt) -> /garmin/prod/HTPASSWD   user: $USER_NAME"
echo "next: deploy/deploy.sh"
