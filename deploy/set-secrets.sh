#!/usr/bin/env bash
# Write the logbook's secrets to SSM Parameter Store (SecureString, read by deploy/server/setup.sh).
#
#   deploy/set-secrets.sh            GARMIN_API_KEY from ./.env; site password reused if it exists, else created
#   deploy/set-secrets.sh --rotate   new random site password + cookie token (update the iPhone Shortcut afterwards;
#                                    every browser / Home Screen app has to log in once more)
#
# The site password (user "logbook") is kept in three places, never printed:
#   1Password    item "Logbook" (login, URL https://logbook.uhl-steine-scherben.org): for you and the phone
#   Keychain     service "logbook", account "logbook": read unattended by deploy/mac/push.py (launchd)
#   SSM          only its bcrypt hash, /garmin/prod/HTPASSWD
# Plus the "remember me" cookie token (64 hex chars), only in SSM /garmin/prod/COOKIE_TOKEN: nginx hands it out as
# a 1-year cookie after a successful basic auth, so the iPhone Home Screen app doesn't ask on every launch.
# An existing password is taken from the Keychain, else from 1Password (e.g. on a new Mac).
# Needs the 1Password CLI (`brew install --cask 1password-cli`, then 1Password → Settings → Developer →
# "Integrate with 1Password CLI"). Optional: OP_VAULT=<vault> (default: your default vault).
# Run deploy/deploy.sh afterwards so the server picks the values up.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-eu-central-1}
USER_NAME=logbook
URL=https://logbook.uhl-steine-scherben.org
OP_ITEM=Logbook
OP_VAULT_ARGS=(); [ -n "${OP_VAULT:-}" ] && OP_VAULT_ARGS=(--vault "$OP_VAULT")

# Fail before changing anything if 1Password isn't usable: the three copies must not drift apart.
command -v op >/dev/null || { echo "1Password CLI 'op' not found: brew install --cask 1password-cli"; exit 1; }
op vault list >/dev/null || { echo "1Password CLI not signed in (enable Settings → Developer → Integrate with 1Password CLI)"; exit 1; }

key=$(sed -n 's/^GARMIN_API_KEY=//p' .env | tr -d "\"'")
[ -n "$key" ] || { echo "GARMIN_API_KEY missing in .env"; exit 1; }

op_has_item() { op item get "$OP_ITEM" ${OP_VAULT_ARGS[@]+"${OP_VAULT_ARGS[@]}"} --format json >/dev/null 2>&1; }
pw=$(security find-generic-password -s logbook -a "$USER_NAME" -w 2>/dev/null || true)
if [ -z "$pw" ] && op_has_item; then
  pw=$(op item get "$OP_ITEM" ${OP_VAULT_ARGS[@]+"${OP_VAULT_ARGS[@]}"} --fields label=password --reveal)
  echo "site password taken from 1Password"
fi
new=false
if [ -z "$pw" ] || [ "${1:-}" = "--rotate" ]; then
  # 24 chars from [A-Za-z0-9]: ~143 bits. Long random beats a slow hash; bcrypt cost 5 keeps per-request checks cheap
  # (basic auth verifies on every request, ~15 per page load).
  pw=$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(24)))')
  new=true
fi

# 1Password: create the item from a private temp template (keeps the password off the command line), or update it.
if ! op_has_item; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT; chmod 700 "$tmp"
  PW="$pw" USER_NAME="$USER_NAME" URL="$URL" OP_ITEM="$OP_ITEM" python3 - > "$tmp/item.json" <<'PY'
import json, os
print(json.dumps({"title": os.environ["OP_ITEM"], "category": "LOGIN",
  "fields": [{"id": "username", "type": "STRING", "purpose": "USERNAME", "label": "username", "value": os.environ["USER_NAME"]},
             {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "label": "password", "value": os.environ["PW"]},
             {"id": "notesPlain", "type": "STRING", "purpose": "NOTES", "label": "notesPlain",
              "value": "Basic auth for the logbook dashboards (garmin repo, deploy/README.md). Managed by deploy/set-secrets.sh."}],
  "urls": [{"label": "website", "primary": True, "href": os.environ["URL"]}]}))
PY
  op item create ${OP_VAULT_ARGS[@]+"${OP_VAULT_ARGS[@]}"} --template "$tmp/item.json" >/dev/null
  echo "1Password: created item '$OP_ITEM'"
elif [ "$(op item get "$OP_ITEM" ${OP_VAULT_ARGS[@]+"${OP_VAULT_ARGS[@]}"} --fields label=password --reveal)" != "$pw" ]; then
  # Single-user Mac: the assignment is briefly visible in the local process list, nowhere else.
  op item edit "$OP_ITEM" ${OP_VAULT_ARGS[@]+"${OP_VAULT_ARGS[@]}"} "password=$pw" >/dev/null
  echo "1Password: updated the password of '$OP_ITEM'"
else
  echo "1Password: '$OP_ITEM' already up to date"
fi

security add-generic-password -U -s logbook -a "$USER_NAME" -l "Logbook site ($URL)" -w "$pw"
$new && echo "new site password stored (1Password + Keychain)"

aws ssm put-parameter --name /garmin/prod/GARMIN_API_KEY --type SecureString --overwrite --value "$key" >/dev/null
echo "GARMIN_API_KEY -> /garmin/prod/GARMIN_API_KEY"
hash=$(printf '%s' "$pw" | htpasswd -niB -C 5 "$USER_NAME" | head -1)
aws ssm put-parameter --name /garmin/prod/HTPASSWD --type SecureString --overwrite --value "$hash" >/dev/null
echo "htpasswd (bcrypt) -> /garmin/prod/HTPASSWD   user: $USER_NAME"
token=$(aws ssm get-parameter --name /garmin/prod/COOKIE_TOKEN --with-decryption --query Parameter.Value --output text 2>/dev/null || true)
if [[ ! "$token" =~ ^[0-9a-f]{64}$ ]] || [ "${1:-}" = "--rotate" ]; then
  token=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  aws ssm put-parameter --name /garmin/prod/COOKIE_TOKEN --type SecureString --overwrite --value "$token" >/dev/null
  echo "new cookie token -> /garmin/prod/COOKIE_TOKEN"
else
  echo "cookie token unchanged (/garmin/prod/COOKIE_TOKEN)"
fi
echo "next: deploy/deploy.sh"
