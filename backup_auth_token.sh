#!/usr/bin/env sh
set -e
set -o pipefail

. <(cat /opt/app/cron.env | sed 's/^/export /')
# pip-installed aws cli
. /opt/app/bin/activate
AKID="{\"s\": {\"opitem\": \"AWS\", \"opfield\": \"${AWS_DEFAULT_REGION}.akid\"}}"
export AWS_ACCESS_KEY_ID="$(echo "${AKID}" | /opt/app/bin/python /opt/app/pylib/cred_tool)"
SAK="{\"s\": {\"opitem\": \"AWS\", \"opfield\": \"${AWS_DEFAULT_REGION}.sak\"}}"
export AWS_SECRET_ACCESS_KEY="$(echo "${SAK}" | /opt/app/bin/python /opt/app/pylib/cred_tool)"
if [ -f /data/snapshot_processor_creds ]; then
  aws s3 cp /data/snapshot_processor_creds s3://tailucas-automation/snapshot_processor_creds --only-show-errors
else
  aws s3 cp s3://tailucas-automation/snapshot_processor_creds /data/snapshot_processor_creds --only-show-errors
fi
unset AWS_ACCESS_KEY_ID
unset AWS_SECRET_ACCESS_KEY
deactivate
