#!/usr/bin/env bash
set -e
set -o pipefail

. <(cat /opt/app/environment.env | sed 's/^/export /')
. /opt/app/bin/activate
if [ -f /data/snapshot_processor_creds ]; then
  aws s3 cp /data/snapshot_processor_creds s3://tailucas-automation/snapshot_processor_creds --only-show-errors
else
  aws s3 cp s3://tailucas-automation/snapshot_processor_creds /data/snapshot_processor_creds --only-show-errors
fi
deactivate
