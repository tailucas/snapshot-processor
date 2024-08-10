#!/usr/bin/env bash
set -eu
set -o pipefail

# client details
echo '{"s": {"opitem": "Google", "opfield": "oath.client_secret"}}' | poetry run /opt/app/cred_tool > /opt/app/client_secrets.json
[ -e /opt/app/client_secrets.json ] && grep -q '[^[:space:]]' /opt/app/client_secrets.json
if test "$(jq type /opt/app/client_secrets.json | tr -d '"')" != "object"; then
  echo "Invalid JSON /opt/app/client_secrets.json"
  exit 1
fi
# we may already have a valid auth token
if [ -n "${GOOGLE_OAUTH_TOKEN:-}" ]; then
  echo "$GOOGLE_OAUTH_TOKEN" > /data/snapshot_processor_creds
fi

set -x

# FTP server setup
FTP_ROOT="/data/ftp"
mkdir -p "${FTP_ROOT}"

# snapshot storage
STORAGE_UPLOADS="${FTP_ROOT}/uploads"
for dir in $(echo "${FTP_CREATE_DIRS:-}" | sed "s/,/ /g"); do
  mkdir -p "${STORAGE_UPLOADS}/${dir}"
done

if [ "${RUN_FTP_SERVER:-}" == "true" ]; then
  cat << EOF >> /opt/app/supervisord.conf
[program:ftp]
command=poetry run ftp
directory=/opt/app/
user=app
autorestart=unexpected
EOF
fi

# Google Refresh Token restore
/opt/app/backup_auth_token.sh
