FROM resin/raspberrypi2-debian:latest
ENV INITSYSTEM on

MAINTAINER db2inst1 <db2inst1@webafrica.org.za>
LABEL Description="snapshot_processor" Vendor="db2inst1" Version="1.0"

COPY ./pipstrap.py /tmp/
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
    ifupdown \
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
    && python /tmp/pipstrap.py

COPY ./config/requirements.txt /tmp/
RUN pip install -r /tmp/requirements.txt

COPY . /app

# disable for boot for now
RUN systemctl disable vsftpd

# Resin systemd
COPY ./config/systemd.launch.service /etc/systemd/system/launch.service.d/app_override.conf

# ftp, ssh, zmq
EXPOSE 21 22 5556 5558
CMD ["/app/entrypoint.sh"]