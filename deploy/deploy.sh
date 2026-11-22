#!/usr/bin/env bash
# Deploy this repo to the shopmate EC2 instance (no SSH: S3 + SSM Run Command), then run deploy/server/setup.sh there.
#
#   deploy/deploy.sh               code only (working tree: tracked + untracked-not-ignored files + dashboards config)
#   deploy/deploy.sh --with-data   + ONE-TIME data bundle: garmin.db snapshot, data/fit, data/weekly_reviews, notes/,
#                                  garmin-dashboards/manual (Strava archive) and build/cache (parsed streams).
#                                  Refused if the Mac's launchd sync is still loaded or the server already has a DB.
#
# Needs: AWS CLI credentials for the account (same as shopmate's terraform), region eu-central-1.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-eu-central-1}

WITH_DATA=false
case "${1:-}" in
  "") ;;
  --with-data) WITH_DATA=true ;;
  *) echo "unknown argument '$1' (only --with-data)"; exit 2 ;;
esac

INSTANCE=$(aws ec2 describe-instances --filters Name=tag:Name,Values=shopmate Name=instance-state-name,Values=running \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
[ "$INSTANCE" != "None" ] || { echo "shopmate instance not running"; exit 1; }
BUCKET=$(aws ssm get-parameter --name /garmin/prod/BUCKET --query Parameter.Value --output text)

STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
export COPYFILE_DISABLE=1   # no macOS ._ files in the tarballs

echo "--- code bundle"
{ git ls-files -co --exclude-standard; echo garmin-dashboards/config.yaml; } | sort -u | while read -r f; do [ -f "$f" ] && echo "$f"; done > "$STAGE/files"
tar -czf "$STAGE/code.tgz" --no-mac-metadata --no-xattrs -T "$STAGE/files"
echo "$(wc -l < "$STAGE/files" | tr -d ' ') files, $(du -h "$STAGE/code.tgz" | cut -f1)"
aws s3 cp --only-show-errors "$STAGE/code.tgz" "s3://$BUCKET/deploy/code.tgz"

SETUP_ARGS=""
if $WITH_DATA; then
  OLD_PLIST=~/Library/LaunchAgents/com.christianuhl.garmin-sync.plist
  if launchctl list 2>/dev/null | grep -q com.christianuhl.garmin-sync || [ -e "$OLD_PLIST" ]; then
    echo "refusing: the Mac's launchd sync (com.christianuhl.garmin-sync) is still installed; it would load again at"
    echo "the next login and keep writing garmin.db. Remove it first:"
    echo "  launchctl bootout gui/\$(id -u)/com.christianuhl.garmin-sync; rm $OLD_PLIST"
    exit 1
  fi
  echo "--- data bundle"
  # Consistent snapshot (WAL mode: copying the file alone could miss committed pages still in garmin.db-wal).
  sqlite3 garmin.db ".backup '$STAGE/garmin.db'"
  tar -czf "$STAGE/data.tgz" --no-mac-metadata --no-xattrs \
    -C "$STAGE" garmin.db \
    -C "$PWD" data/fit data/weekly_reviews notes garmin-dashboards/manual garmin-dashboards/build/cache
  echo "$(du -h "$STAGE/data.tgz" | cut -f1)"
  DATA_KEY="deploy/data-$(date +%Y%m%d%H%M%S).tgz"
  aws s3 cp --only-show-errors "$STAGE/data.tgz" "s3://$BUCKET/$DATA_KEY"
  SETUP_ARGS="--data s3://$BUCKET/$DATA_KEY"
fi

echo "--- remote setup on $INSTANCE"
cat > "$STAGE/params.json" <<EOF
{"commands": [
  "set -euo pipefail",
  "aws s3 cp --only-show-errors s3://$BUCKET/deploy/code.tgz /tmp/garmin-code.tgz",
  "mkdir -p /opt/garmin && tar -xzf /tmp/garmin-code.tgz -C /opt/garmin --no-same-owner && rm -f /tmp/garmin-code.tgz",
  "bash /opt/garmin/deploy/server/setup.sh $SETUP_ARGS"
], "executionTimeout": ["1800"]}
EOF
CMD=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
  --comment "garmin deploy" --parameters "file://$STAGE/params.json" --query Command.CommandId --output text)
while :; do
  sleep 5
  STATUS=$(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$INSTANCE" --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Pending|InProgress|Delayed) ;; *) break ;; esac
done
aws ssm get-command-invocation --command-id "$CMD" --instance-id "$INSTANCE" --query StandardOutputContent --output text
ERR=$(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$INSTANCE" --query StandardErrorContent --output text)
[ -n "$ERR" ] && [ "$ERR" != "None" ] && echo "stderr: $ERR"
echo "status: $STATUS"
[ "$STATUS" = Success ]
