#!/bin/bash
set -eu
set -o pipefail

# Resin API key
export RESIN_API_KEY="${RESIN_API_KEY:-$API_KEY_RESIN}"
# root user access, prefer key
mkdir -p /root/.ssh/
if [ -n "$SSH_AUTHORIZED_KEY" ]; then
  echo "$SSH_AUTHORIZED_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
elif [ -n "$ROOT_PASSWORD" ]; then
  echo "root:${ROOT_PASSWORD}" | chpasswd
  sed -i 's/PermitRootLogin without-password/PermitRootLogin yes/' /etc/ssh/sshd_config
  # SSH login fix. Otherwise user is kicked off after login
  sed 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' -i /etc/pam.d/sshd
fi
# https://bugs.launchpad.net/ubuntu/+source/openssh/+bug/45234
mkdir -p /run/sshd
# reload sshd
service ssh reload

# client details
echo "$GOOGLE_CLIENT_SECRETS" > /opt/app/client_secrets.json
# we may already have a valid auth token
if [ -n "${GOOGLE_OAUTH_TOKEN:-}" ]; then
  echo "$GOOGLE_OAUTH_TOKEN" > /data/snapshot_processor_creds
fi
echo "$API_IBM_TTS" > /opt/app/ibm_tts_creds.json

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
chown -R "${APP_USER}:${APP_GROUP}" /opt/app/
# non-volatile storage
chown -R "${APP_USER}:${APP_GROUP}" /data/

TZ_CACHE=/data/localtime
# a valid symlink
if [ -h "$TZ_CACHE" ] && [ -e "$TZ_CACHE" ]; then
  cp -a "$TZ_CACHE" /etc/localtime
fi
# set the timezone
(tzupdate && cp -a /etc/localtime "$TZ_CACHE") || [ -e "$TZ_CACHE" ]

# reset hostname (in a way that works)
# https://forums.resin.io/t/read-only-file-system-when-calling-setstatichostname-via-dbus/1578/10
curl -X PATCH --header "Content-Type:application/json" \
  --data '{"network": {"hostname": "'${RESIN_DEVICE_NAME_AT_INIT}'"}}' \
  "$RESIN_SUPERVISOR_ADDRESS/v1/device/host-config?apikey=$RESIN_SUPERVISOR_API_KEY"
echo "$RESIN_DEVICE_NAME_AT_INIT" > /etc/hostname
echo "127.0.1.1 ${RESIN_DEVICE_NAME_AT_INIT}" >> /etc/hosts

cp /opt/app/config/rsyslog.conf /etc/rsyslog.conf
if [ -n "${RSYSLOG_SERVER:-}" ]; then
  set +x
  if [ -n "${RSYSLOG_TOKEN:-}" ] && ! grep -q "$RSYSLOG_TOKEN" /etc/rsyslog.d/custom.conf; then
    echo "\$template LogentriesFormat,\"${RSYSLOG_TOKEN} %HOSTNAME% %syslogtag%%msg%\n\"" >> /etc/rsyslog.d/custom.conf
    RSYSLOG_TEMPLATE=";LogentriesFormat"
  fi
  echo "*.*          @@${RSYSLOG_SERVER}${RSYSLOG_TEMPLATE:-}" >> /etc/rsyslog.d/custom.conf
  set -x
  # bounce rsyslog with the new configuration
  service rsyslog restart
fi

# log archival (no tee for secrets)
if [ -d /var/awslogs/etc/ ]; then
  cat /var/awslogs/etc/aws.conf | /opt/app/config_interpol /opt/app/config/aws.conf > /var/awslogs/etc/aws.conf.new
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
# user sub-directories
for dir in $(echo "${FTP_CREATE_DIRS:-}" | sed "s/,/ /g"); do
  mkdir -p "${STORAGE_UPLOADS}/${dir}"
done
chown -R "${FTP_USER}:${APP_GROUP}" "${FTP_HOME}/"
chown -R "${FTP_USER}:${APP_GROUP}" "${STORAGE_ROOT}/"
chmod a-w "${FTP_ROOT}"
# allow all in the same group to write
chmod -R g+w "${FTP_ROOT}"

echo "${FTP_USER}:${FTP_PASSWORD}" | chpasswd

cat /etc/vsftpd.conf | /opt/app/config_interpol /opt/app/config/vsftpd.conf | sort | tee /etc/vsftpd.conf.new
mv /etc/vsftpd.conf /etc/vsftpd.conf.backup
mv /etc/vsftpd.conf.new /etc/vsftpd.conf
# secure_chroot_dir
mkdir -p /var/run/vsftpd/empty


# configuration update
for iface in wlan0 eth0; do
  export ETH0_IP="$(/sbin/ifconfig ${iface} | grep 'inet' | awk '{ print $2 }' | cut -f2 -d ':')"
  if [ -n "$ETH0_IP" ]; then
    break
  fi
done
# application configuration (no tee for secrets)
cat /opt/app/config/app.conf | /opt/app/config_interpol > "/opt/app/${APP_NAME}.conf"
unset ETH0_IP

cat /opt/app/config/cleanup_snapshots | sed "s~__STORAGE__~${STORAGE_UPLOADS}/~g" > /etc/cron.d/cleanup_snapshots

# tts samples
cp -rv /opt/app/tts_samples/ /data/

# so app user can make the noise
adduser "${APP_USER}" audio
# set the volume
amixer set PCM "${TTS_VOLUME_PERCENT:-100}%"

# Load app environment, overriding HOME and USER
# https://www.freedesktop.org/software/systemd/man/systemd.exec.html
cat /etc/docker.env | egrep -v "^HOME|^USER" > /opt/app/environment.env
echo "HOME=/data/" >> /opt/app/environment.env
echo "USER=${APP_USER}" >> /opt/app/environment.env

echo "export HISTFILE=/data/.bash_history" >> /etc/bash.bashrc

# link in libbcm_host.so required by mplayer
sudo ln -fs /opt/vc/lib/libbcm_host.so /usr/lib/libbcm_host.so

# systemd configuration
for systemdsvc in app; do
  if [ ! -e "/etc/systemd/system/${systemdsvc}.service" ]; then
    cat "/opt/app/config/systemd.${systemdsvc}.service" | /opt/app/config_interpol | tee "/etc/systemd/system/${systemdsvc}.service"
    chmod 664 "/etc/systemd/system/${systemdsvc}.service"
    systemctl daemon-reload
    systemctl enable "${systemdsvc}"
  fi
done
# vsftpd can be enabled now
for systemdsvc in vsftpd; do
  systemctl enable "${systemdsvc}"
done
for systemdsvc in cron vsftpd app; do
  systemctl start "${systemdsvc}"&
done