#!/usr/bin/env bash
set -e
set -o pipefail

. <(cat /opt/app/environment.env | sed 's/^/export /')
. /opt/app/bin/activate
export AWS_ACCESS_KEY_ID="$(/opt/app/bin/python /opt/app/cred_tool <<< '{"s": {"opitem": "AWS", "opfield": "${AWS_DEFAULT_REGION}.akid"}}')"
export AWS_SECRET_ACCESS_KEY="$(/opt/app/bin/python /opt/app/cred_tool <<< '{"s": {"opitem": "AWS", "opfield": "${AWS_DEFAULT_REGION}.sak"}}')"
if [ -f /data/snapshot_processor_creds ]; then
  aws s3 cp /data/snapshot_processor_creds s3://tailucas-automation/snapshot_processor_creds --only-show-errors
else
  aws s3 cp s3://tailucas-automation/snapshot_processor_creds /data/snapshot_processor_creds --only-show-errors
fi
unset AWS_ACCESS_KEY_ID
unset AWS_SECRET_ACCESS_KEY
deactivate
