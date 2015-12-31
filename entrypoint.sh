#!/bin/bash
set -eu

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

# client details
echo "$GOOGLE_CLIENT_SECRETS" > /app/client_secrets.json
# we may already have a valid auth token
if [ -n "${GOOGLE_OAUTH_TOKEN:-}" ]; then
  echo "$GOOGLE_OAUTH_TOKEN" > /data/snapshot_processor_creds
fi
echo "$API_IBM_TTS" > /app/ibm_tts_creds.json


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


TZ_CACHE=/data/localtime
# a valid symlink
if [ -h "$TZ_CACHE" ] && [ -e "$TZ_CACHE" ]; then
  cp -a "$TZ_CACHE" /etc/localtime
else
  # set the timezone
  tzupdate
  cp -a /etc/localtime "$TZ_CACHE"
fi


# remote system logging
HN_CACHE=/data/hostname
if [ -e "$HN_CACHE" ]; then
  export DEVICE_NAME="$(cat "$HN_CACHE")"
else
  export DEVICE_NAME="$(python /app/resin --get-device-name)"
  echo "$DEVICE_NAME" > "$HN_CACHE"
fi
echo "$DEVICE_NAME" > /etc/hostname
# apply the new hostname
/etc/init.d/hostname.sh start
# update hosts
echo "127.0.1.1 ${DEVICE_NAME}" >> /etc/hosts
unset DEVICE_NAME

if [ -n "${RSYSLOG_SERVER:-}" ] && ! grep -q "$RSYSLOG_SERVER" /etc/rsyslog.conf; then
  echo "*.*          @${RSYSLOG_SERVER}" | tee -a /etc/rsyslog.conf
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
ln -s "$STORAGE_ROOT" "$FTP_ROOT"
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
export ETH0_IP="$(/sbin/ifconfig eth0 | grep 'inet addr' | awk '{ print $2 }' | cut -f2 -d ':')"
SUB_CACHE=/data/sub_src
if [ -e "$SUB_CACHE" ]; then
  export SUB_SRC="$(cat "$SUB_CACHE")"
else
  export SUB_SRC="$(python /app/resin --get-devices | grep -v "$ETH0_IP" | paste -d, -s)"
  echo "$SUB_SRC" > "$SUB_CACHE"
fi
# application configuration (no tee for secrets)
cat /app/config/app.conf | python /app/config_interpol > "/app/${APP_NAME}.conf"
unset ETH0_IP
unset SUB_SRC


cat /app/config/cleanup_snapshots | sed 's/__STORAGE__/'"${STORAGE_UPLOADS//\//\/}\/"'/g' > /etc/cron.d/cleanup_snapshots

# tts samples
cp -rv /app/tts_samples/ /data/

# so app user can make the noise
adduser "${APP_USER}" audio

# apcupsd
sed -e '/ISCONFIGURED/ s/^#*/#/' -i /etc/default/apcupsd
echo "ISCONFIGURED=yes" >> /etc/default/apcupsd
sed -e '/DEVICE/ s/^#*/#/' -i /etc/apcupsd/apcupsd.conf
echo "DEVICE ${UPS_USB}" >> /etc/apcupsd/apcupsd.conf
service apcupsd start

# I'm the supervisor
cat /app/config/supervisord.conf | python /app/config_interpol | tee /etc/supervisor/conf.d/supervisord.conf
/usr/bin/supervisord