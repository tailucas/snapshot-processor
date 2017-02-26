FROM resin/raspberrypi2-debian:latest
ENV INITSYSTEM on

MAINTAINER db2inst1 <db2inst1@webafrica.org.za>
LABEL Description="snapshot_processor" Vendor="db2inst1" Version="1.0"

COPY . /app
RUN apt-get clean && apt-get update && apt-get install -y --no-install-recommends \
    alsa-utils \
    apcupsd \
    ca-certificates \
    cron \
    cpp \
    curl \
    dbus \
    g++ \
    gcc \
    git \
    htop \
    less \
    libffi-dev \
    libraspberrypi-bin \
    libssl-dev \
    lsof \
    man-db \
    manpages \
    mediainfo \
    mplayer \
    net-tools \
    openssh-server \
    openssl \
    psmisc \
    python-dbus \
    python-gammu \
    python-pip \
    python2.7 \
    python2.7-dev \
    rsyslog \
    ssl-cert \
    strace \
    vim \
    vsftpd \
    wavemon \
    wget \
    # pip 8
    && python /app/pipstrap.py

RUN pip install -r /app/config/requirements.txt

# disable for boot for now
RUN systemctl disable vsftpd

# ftp, ssh, zmq
EXPOSE 21 22 5556 5558
CMD ["./app/entrypoint.sh"]