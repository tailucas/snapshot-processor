FROM resin/raspberry-pi2-debian:stretch
ENV INITSYSTEM on

MAINTAINER db2inst1 <db2inst1@webafrica.org.za>
LABEL Description="snapshot_processor" Vendor="db2inst1" Version="1.0"

# http://unix.stackexchange.com/questions/339132/reinstall-man-pages-fix-man
RUN rm -f /etc/dpkg/dpkg.cfg.d/01_nodoc /etc/dpkg/dpkg.cfg.d/docker
RUN apt-get clean && apt-get update && apt-get install -y --no-install-recommends \
    alsa-utils \
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
    libzmq3-dev \
    lsof \
    man-db \
    manpages \
    mediainfo \
    mplayer \
    net-tools \
    openssh-server \
    openssl \
    psmisc \
    python3-dbus \
    python3 \
    python3-dev \
    python3-pil \
    python3-pip \
    python3-setuptools \
    python3-venv \
    rsyslog \
    ssl-cert \
    strace \
    tree \
    vim \
    vsftpd \
    wavemon \
    wget

# python3 default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

COPY . /opt/app

# setup
RUN /opt/app/app_setup.sh

# disable for boot for now
RUN systemctl disable vsftpd

# Resin systemd
COPY ./config/systemd.launch.service /etc/systemd/system/launch.service.d/app_override.conf

# ftp, ssh, zmq
EXPOSE 21 22 5556 5558
CMD ["/opt/app/entrypoint.sh"]