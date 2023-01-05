#!/bin/bash
set -eu
set -o pipefail

# client details
echo "$(/opt/app/bin/python /opt/app/pylib/cred_tool <<< '{"s": {"opitem": "Google", "opfield": "oath.client_secret"}}')" > /opt/app/client_secrets.json
[ -e /opt/app/client_secrets.json ] && grep -q '[^[:space:]]' /opt/app/client_secrets.json
[[ $(cat /opt/app/client_secrets.json | jq type | tr -d '"') == "object" ]]
# we may already have a valid auth token
if [ -n "${GOOGLE_OAUTH_TOKEN:-}" ]; then
  echo "$GOOGLE_OAUTH_TOKEN" > /data/snapshot_processor_creds
fi

# FTP server setup
FTP_ROOT="/data/ftp"
mkdir -p "${FTP_ROOT}"
#echo -e "${FTP_USER}:${FTP_PASS}\n" > /opt/app/ftp_authz.txt
cat << EOF >> /opt/app/supervisord.conf
[program:ftp]
command=/opt/app/bin/python /opt/app/ftp_server
user=app
autorestart=unexpected
EOF

# Google Refresh Token restore
if [ ! -f /data/snapshot_processor_creds ]; then
  /opt/app/backup_auth_token.sh
fi

set -x
# snapshot storage
STORAGE_UPLOADS="${FTP_ROOT}/uploads"
for dir in $(echo "${FTP_CREATE_DIRS:-}" | sed "s/,/ /g"); do
  mkdir -p "${STORAGE_UPLOADS}/${dir}"
done
