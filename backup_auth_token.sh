#!/usr/bin/env bash
set -eu
set -o pipefail

me="$(basename "$0")"
cd "$(dirname "$0")"

log () {
  echo "${me} $1"
}

# source environment
. <(sed 's/^/export /' /opt/app/cron.env)
# generate AWS configuration
log "Generating AWS config (${AWS_CONFIG_FILE:-default}) and credentials (${AWS_SHARED_CREDENTIALS_FILE:-default})..."
uv run aws_configure

if [ -f /data/snapshot_processor_creds ]; then
  uv run aws s3 cp /data/snapshot_processor_creds "s3://${BACKUP_S3_BUCKET}/snapshot_processor_creds" --only-show-errors
else
  uv run aws s3 cp "s3://${BACKUP_S3_BUCKET}/snapshot_processor_creds" /data/snapshot_processor_creds --only-show-errors
fi
