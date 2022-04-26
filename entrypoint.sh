#!/bin/bash
set -eu
set -o pipefail

# host heartbeat, must fail if variable is unset
echo "Installing heartbeat to ${HC_PING_URL}"
cp /opt/app/config/healthchecks_heartbeat /etc/cron.d/healthchecks_heartbeat

# client details
echo "$(/opt/app/bin/python /opt/app/pylib/cred_tool <<< '{"s": {"opitem": "Google", "opfield": "oath.client_secret"}}')" > /opt/app/client_secrets.json
[ -e /opt/app/client_secrets.json ] && grep -q '[^[:space:]]' /opt/app/client_secrets.json
[[ $(cat /opt/app/client_secrets.json | jq type | tr -d '"') == "object" ]]
# we may already have a valid auth token
if [ -n "${GOOGLE_OAUTH_TOKEN:-}" ]; then
  echo "$GOOGLE_OAUTH_TOKEN" > /data/snapshot_processor_creds
fi

# Create /etc/docker.env
if [ ! -e /etc/docker.env ]; then
  # https://github.com/balena-io-library/base-images/blob/b4fc5c21dd1e28c21e5661f65809c90ed7605fe6/examples/INITSYSTEM/systemd/systemd/entry.sh
  for var in $(compgen -e); do
    printf '%q=%q\n' "$var" "${!var}"
  done > /etc/docker.env
fi

set -x

# Run user
export APP_USER="${APP_USER:-app}"
export APP_GROUP="${APP_GROUP:-app}"

# groups
groupadd -f -r "${APP_GROUP}"

# non-root users
id -u "${APP_USER}" || useradd -r -g "${APP_GROUP}" "${APP_USER}"
chown "${APP_USER}:${APP_GROUP}" /opt/app/*
# non-volatile storage
chown -R "${APP_USER}:${APP_GROUP}" /data/
# home
mkdir -p "/home/${APP_USER}/.aws/"
chown -R "${APP_USER}:${APP_GROUP}" "/home/${APP_USER}/"
# access to nVidia hardware
usermod -a -G video "${APP_USER}"
# access to Jetson Nano fan PWM (0-255)
if [ -e /sys/devices/pwm-fan/target_pwm ]; then
  chown "${APP_USER}" /sys/devices/pwm-fan/target_pwm
fi
# allow reading of fan RPM, only set if unset
if [ -e /sys/devices/pwm-fan/tach_enable ]; then
  TACH_ENABLE=$(cat /sys/devices/pwm-fan/tach_enable)
  if [ "$TACH_ENABLE" == "0" ]; then
    echo "1" > /sys/devices/pwm-fan/tach_enable
  fi
fi
# AWS configuration (no tee for secrets)
cat /opt/app/config/aws-config | /opt/app/pylib/config_interpol > "/home/${APP_USER}/.aws/config"
# patch botoflow to work-around
# AttributeError: 'Endpoint' object has no attribute 'timeout'
PY_BASE_WORKER="$(find /opt/app/ -name base_worker.py)"
patch -f -u "$PY_BASE_WORKER" -i /opt/app/config/base_worker.patch || true

TZ_CACHE=/data/localtime
# a valid symlink
if [ -h "$TZ_CACHE" ] && [ -e "$TZ_CACHE" ]; then
  cp -a "$TZ_CACHE" /etc/localtime
fi
# set the timezone
(tzupdate && cp -a /etc/localtime "$TZ_CACHE") || [ -e "$TZ_CACHE" ]

# rsyslog
if [ -n "${RSYSLOG_SERVER:-}" ]; then
  cat << EOF > /etc/rsyslog.d/custom.conf
\$PreserveFQDN on
\$ActionQueueFileName queue
\$ActionQueueMaxDiskSpace 1g
\$ActionQueueSaveOnShutdown on
\$ActionQueueType LinkedList
\$ActionResumeRetryCount -1
*.* @${RSYSLOG_SERVER}${RSYSLOG_TEMPLATE:-}
EOF
fi
# logentries
if [ -n "${RSYSLOG_LOGENTRIES:-}" ]; then
  set +x
  RSYSLOG_LOGENTRIES_TOKEN="$(/opt/app/bin/python /opt/app/pylib/cred_tool <<< '{"s": {"opitem": "Logentries", "opfield": "${APP_NAME}.token"}}')"
  if [ -n "${RSYSLOG_LOGENTRIES_TOKEN:-}" ] && ! grep -q "$RSYSLOG_LOGENTRIES_TOKEN" /etc/rsyslog.d/logentries.conf; then
    echo "\$template LogentriesFormat,\"${RSYSLOG_LOGENTRIES_TOKEN} %HOSTNAME% %syslogtag%%msg%\n\"" >> /etc/rsyslog.d/logentries.conf
    RSYSLOG_TEMPLATE=";LogentriesFormat"
  fi
  echo "*.*          @@${RSYSLOG_LOGENTRIES_SERVER}${RSYSLOG_TEMPLATE:-}" >> /etc/rsyslog.d/logentries.conf
  unset RSYSLOG_LOGENTRIES_TOKEN
  set -x
fi

# FTP server setup
FTP_USER="$(/opt/app/bin/python /opt/app/pylib/cred_tool <<< '{"s": {"opitem": "FTP", "opfield": ".username"}}')"
id -u "${FTP_USER}" || useradd -r -g "${APP_GROUP}" "${FTP_USER}"
FTP_HOME="/home/${FTP_USER}"
mkdir -p "${FTP_HOME}/"
export FTP_ROOT="${FTP_HOME}/ftp"
export STORAGE_ROOT="/data/ftp"
STORAGE_UPLOADS="${STORAGE_ROOT}/uploads"
mkdir -p "${STORAGE_UPLOADS}"
if [ ! -h "$FTP_ROOT" ]; then
  ln -s "$STORAGE_ROOT" "$FTP_ROOT"
fi
# user sub-directories
for dir in $(echo "${FTP_CREATE_DIRS:-}" | sed "s/,/ /g"); do
  mkdir -p "${STORAGE_UPLOADS}/${dir}"
done
chown -R "${FTP_USER}:${APP_GROUP}" "${FTP_HOME}/"
chown -R "${FTP_USER}:${APP_GROUP}" "${STORAGE_ROOT}/"
chmod a-w "${FTP_ROOT}"
# allow all in the same group to write
chmod -R g+w "${FTP_ROOT}"
set +x
echo "${FTP_USER}:$(/opt/app/bin/python /opt/app/pylib/cred_tool <<< '{"s": {"opitem": "FTP", "opfield": ".password"}}')" | chpasswd
set -x
unset FTP_USER

cat /etc/vsftpd.conf | /opt/app/pylib/config_interpol /opt/app/config/vsftpd.conf | sort | tee /etc/vsftpd.conf.new
mv /etc/vsftpd.conf /etc/vsftpd.conf.backup
mv /etc/vsftpd.conf.new /etc/vsftpd.conf
# secure_chroot_dir
mkdir -p /var/run/vsftpd/empty


# application configuration (no tee for secrets)
cat /opt/app/config/app.conf | /opt/app/pylib/config_interpol > "/opt/app/${APP_NAME}.conf"
cat /opt/app/config/cleanup_snapshots | sed "s~__STORAGE__~${STORAGE_UPLOADS}/~g" > /etc/cron.d/cleanup_snapshots
cat /opt/app/config/backup_auth_token | sed "s~__APP_USER__~${APP_USER}~g" > /etc/cron.d/backup_auth_token

# Load app environment, overriding HOME and USER
# https://www.freedesktop.org/software/systemd/man/systemd.exec.html
cat /etc/docker.env | egrep -v "^HOME|^USER" > /opt/app/environment.env
echo "HOME=/data/" >> /opt/app/environment.env
echo "USER=${APP_USER}" >> /opt/app/environment.env

# Google Refresh Token restore
if [ ! -f /data/snapshot_processor_creds ]; then
  /opt/app/backup_auth_token.sh
  chown "${APP_USER}:${APP_GROUP}" /data/snapshot_processor_creds
fi

echo "export HISTFILE=/data/.bash_history" >> /etc/bash.bashrc

# systemd configuration
for systemdsvc in app; do
  if [ ! -e "/etc/systemd/system/${systemdsvc}.service" ]; then
    cat "/opt/app/config/systemd.${systemdsvc}.service" | /opt/app/pylib/config_interpol | tee "/etc/systemd/system/${systemdsvc}.service"
    chmod 664 "/etc/systemd/system/${systemdsvc}.service"
    systemctl enable "${systemdsvc}"
  fi
done

# output some useful Jetson stats before hand-off
if [ -e /sys/devices/pwm-fan/pwm_rpm_table ]; then
  cat /sys/devices/pwm-fan/pwm_rpm_table
fi
if [ -e /sys/devices/57000000.gpu/devfreq/57000000.gpu/trans_stat ]; then
  cat /sys/devices/57000000.gpu/devfreq/57000000.gpu/trans_stat
fi

# replace this entrypoint with systemd init scope
exec env DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket SYSTEMD_LOG_LEVEL=info /lib/systemd/systemd quiet systemd.show_status=0
