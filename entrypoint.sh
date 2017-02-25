#!/bin/bash
set -eu
set -o pipefail

# Resin API key
export RESIN_API_KEY="${RESIN_API_KEY:-$API_KEY_RESIN}"
# root user access, prefer key
if [ -n "$SSH_AUTHORIZED_KEY" ]; then
  echo "$SSH_AUTHORIZED_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
elif [ -n "$ROOT_PASSWORD" ]; then
  echo "root:${ROOT_PASSWORD}" | chpasswd
  sed -i 's/PermitRootLogin without-password/PermitRootLogin yes/' /etc/ssh/sshd_config
  # SSH login fix. Otherwise user is kicked off after login
  sed 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' -i /etc/pam.d/sshd
fi
# reload sshd
service ssh reload

# client details
echo "$GOOGLE_CLIENT_SECRETS" > /app/client_secrets.json
# we may already have a valid auth token
if [ -n "${GOOGLE_OAUTH_TOKEN:-}" ]; then
  echo "$GOOGLE_OAUTH_TOKEN" > /data/snapshot_processor_creds
fi
echo "$API_IBM_TTS" > /app/ibm_tts_creds.json

# aws code commit
if [ -n "${AWS_REPO_SSH_KEY_ID:-}" ]; then
  # ssh
  echo "$AWS_REPO_SSH_PRIVATE_KEY" | base64 -d > /root/.ssh/codecommit_rsa
  chmod 600 /root/.ssh/codecommit_rsa
  cat << EOF >> /root/.ssh/config
StrictHostKeyChecking=no
Host git-codecommit.*.amazonaws.com
  User $AWS_REPO_SSH_KEY_ID
  IdentityFile /root/.ssh/codecommit_rsa
EOF
  chmod 600 /root/.ssh/config
fi

set -x


# Run user
export APP_USER="${APP_USER:-app}"
export APP_GROUP="${APP_GROUP:-app}"

# groups
groupadd -f -r "${APP_GROUP}"

# non-root users
id -u "${APP_USER}" || useradd -r -g "${APP_GROUP}" "${APP_USER}"
chown -R "${APP_USER}:${APP_GROUP}" /app/
# non-volatile storage
chown -R "${APP_USER}:${APP_GROUP}" /data/
# pidfile
chown "${APP_USER}" /var/run/

TZ_CACHE=/data/localtime
# a valid symlink
if [ -h "$TZ_CACHE" ] && [ -e "$TZ_CACHE" ]; then
  cp -a "$TZ_CACHE" /etc/localtime
fi
# set the timezone
(tzupdate && cp -a /etc/localtime "$TZ_CACHE") || [ -e "$TZ_CACHE" ]

# remote system logging
HN_CACHE=/data/hostname
if [ -e "$HN_CACHE" ]; then
  DEVICE_NAME="$(cat "$HN_CACHE")"
  # reject if there is a space
  space_pattern=" |'"
  if [[ $DEVICE_NAME =~ $space_pattern ]]; then
    unset DEVICE_NAME
  else
    export DEVICE_NAME
  fi
fi
# refresh the device name and bail unless cached
export DEVICE_NAME="$(python /app/resin --get-device-name)" || [ -n "${DEVICE_NAME:-}" ]
echo "$DEVICE_NAME" > "$HN_CACHE"
echo "$DEVICE_NAME" > /etc/hostname
# apply the new hostname
hostnamectl set-hostname "$DEVICE_NAME"
# update hosts
echo "127.0.1.1 ${DEVICE_NAME}" >> /etc/hosts

cp /app/config/rsyslog.conf /etc/rsyslog.conf
if [ -n "${RSYSLOG_SERVER:-}" ]; then
  set +x
  if [ -n "${RSYSLOG_TOKEN:-}" ] && ! grep -q "$RSYSLOG_TOKEN" /etc/rsyslog.d/custom.conf; then
    echo "\$template LogentriesFormat,\"${RSYSLOG_TOKEN} %HOSTNAME% %syslogtag%%msg%\n\"" >> /etc/rsyslog.d/custom.conf
    RSYSLOG_TEMPLATE=";LogentriesFormat"
  fi
  echo "*.*          @@${RSYSLOG_SERVER}${RSYSLOG_TEMPLATE:-}" >> /etc/rsyslog.d/custom.conf
  set -x
fi
# bounce rsyslog with the new configuration
service rsyslog restart

# log archival (no tee for secrets)
if [ -d /var/awslogs/etc/ ]; then
  cat /var/awslogs/etc/aws.conf | python /app/config_interpol /app/config/aws.conf > /var/awslogs/etc/aws.conf.new
  mv /var/awslogs/etc/aws.conf /var/awslogs/etc/aws.conf.backup
  mv /var/awslogs/etc/aws.conf.new /var/awslogs/etc/aws.conf
fi

# remove unnecessary kernel drivers
rmmod w1_gpio||true

# FTP server setup
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
chown -R "${FTP_USER}:${APP_GROUP}" "${FTP_HOME}/"
chown -R "${FTP_USER}:${APP_GROUP}" "${STORAGE_ROOT}/"
chmod a-w "${FTP_ROOT}"

echo "${FTP_USER}:${FTP_PASSWORD}" | chpasswd

cat /etc/vsftpd.conf | python /app/config_interpol /app/config/vsftpd.conf | sort | tee /etc/vsftpd.conf.new
mv /etc/vsftpd.conf /etc/vsftpd.conf.backup
mv /etc/vsftpd.conf.new /etc/vsftpd.conf
# secure_chroot_dir
mkdir -p /var/run/vsftpd/empty


# configuration update
for iface in eth0 wlan0; do
  export ETH0_IP="$(/sbin/ifconfig ${iface} | grep 'inet addr' | awk '{ print $2 }' | cut -f2 -d ':')"
  if [ -n "$ETH0_IP" ]; then
    break
  fi
done
SUB_CACHE=/data/sub_src
if [ -e "$SUB_CACHE" ]; then
  export SUB_SRC="$(cat "$SUB_CACHE")"
fi
# get the latest sources and bail unless cached
export SUB_SRC="$(python /app/resin --get-devices | grep -v "$ETH0_IP" | paste -d, -s)" || [ -n "${SUB_SRC:-}" ]
echo "$SUB_SRC" > "$SUB_CACHE"
# application configuration (no tee for secrets)
cat /app/config/app.conf | python /app/config_interpol > "/app/${APP_NAME}.conf"
unset ETH0_IP
unset SUB_SRC

cat /app/config/cleanup_snapshots | sed "s~__STORAGE__~${STORAGE_UPLOADS}/~g" > /etc/cron.d/cleanup_snapshots

# tts samples
cp -rv /app/tts_samples/ /data/

# so app user can make the noise
adduser "${APP_USER}" audio

# apcupsd
sed -e '/ISCONFIGURED/ s/^#*/#/' -i /etc/default/apcupsd
echo "ISCONFIGURED=yes" >> /etc/default/apcupsd
sed -e '/DEVICE/ s/^#*/#/' -i /etc/apcupsd/apcupsd.conf
echo "DEVICE ${UPS_USB}" >> /etc/apcupsd/apcupsd.conf
# to catch user self-test
echo "POLLTIME 5" >> /etc/apcupsd/apcupsd.conf
service apcupsd start
# Pygtail log tailer
touch /var/log/apcupsd.events.offset
chown "${APP_USER}:${APP_GROUP}" /var/log/apcupsd.events.offset

# Used by resin-sdk Settings
export USER="${APP_USER}"
export HOME=/data/
echo "export HISTFILE=/data/.bash_history_\${USER}" >> /etc/bash.bashrc

# link in libbcm_host.so required by mplayer
sudo ln -fs /opt/vc/lib/libbcm_host.so /usr/lib/libbcm_host.so

# systemd configuration
for systemdsvc in app; do
  cat "/app/config/systemd.${systemdsvc}.service" | python /app/config_interpol | tee "/etc/systemd/system/${systemdsvc}.service"
  chmod 664 "/etc/systemd/system/${systemdsvc}.service"
done
systemctl daemon-reload
for systemdsvc in cron apcupsd vsftpd cron app; do
  systemctl start "${systemdsvc}"
done