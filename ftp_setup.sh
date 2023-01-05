#!/usr/bin/env bash
set -eu
set -o pipefail

set -x

# assumes user/pass set up already in Dockerfile

# FTP server setup
FTP_HOME="/home/${FTP_USER}"
mkdir -p "${FTP_HOME}/"
export FTP_ROOT="${FTP_HOME}/ftp"
STORAGE_UPLOADS="${FTP_STORAGE_DIR}/uploads"
mkdir -p "${STORAGE_UPLOADS}"
if [ ! -h "$FTP_ROOT" ]; then
  ln -s "$FTP_STORAGE_DIR" "$FTP_ROOT"
fi
# user sub-directories
for dir in $(echo "${FTP_CREATE_DIRS:-}" | sed "s/,/ /g"); do
  mkdir -p "${STORAGE_UPLOADS}/${dir}"
done
chown -R "${FTP_USER}:app" "${FTP_HOME}/"
chown -R "${FTP_USER}:app" "${FTP_STORAGE_DIR}/"
chmod a-w "${FTP_ROOT}"
# allow all in the same group to write
chmod -R g+w "${FTP_ROOT}"

cat /opt/app/config/cleanup_snapshots | sed "s~__STORAGE__~${STORAGE_UPLOADS}/~g" > /etc/cron.d/cleanup_snapshots

cat /etc/vsftpd.conf | /opt/app/pylib/config_interpol /opt/app/config/vsftpd.conf | sort | tee /etc/vsftpd.conf.new
mv /etc/vsftpd.conf /etc/vsftpd.conf.backup
mv /etc/vsftpd.conf.new /etc/vsftpd.conf
# secure_chroot_dir
mkdir -p /var/run/vsftpd/empty

cat << EOF >> /opt/app/supervisord.conf
[program:vsftpd]
command=/usr/sbin/vsftpd /etc/vsftpd.conf
autorestart=unexpected
EOF
