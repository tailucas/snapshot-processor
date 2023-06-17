#!/usr/bin/env sh
set -eu
set -o pipefail
cd "$(dirname "$0")"

AKID="{\"s\": {\"opitem\": \"AWS\", \"opfield\": \"${AWS_DEFAULT_REGION}.akid\"}}"
export AWS_ACCESS_KEY_ID="$(echo "${AKID}" | poetry run /opt/app/pylib/cred_tool)"
SAK="{\"s\": {\"opitem\": \"AWS\", \"opfield\": \"${AWS_DEFAULT_REGION}.sak\"}}"
export AWS_SECRET_ACCESS_KEY="$(echo "${SAK}" | poetry run /opt/app/pylib/cred_tool)"
if [ -f /data/snapshot_processor_creds ]; then
  poetry run aws s3 cp /data/snapshot_processor_creds "s3://${BACKUP_S3_BUCKET}/snapshot_processor_creds" --only-show-errors
else
  poetry run aws s3 cp "s3://${BACKUP_S3_BUCKET}/snapshot_processor_creds" /data/snapshot_processor_creds --only-show-errors
fi
unset AWS_ACCESS_KEY_ID
unset AWS_SECRET_ACCESS_KEY
