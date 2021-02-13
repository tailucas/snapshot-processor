FROM balenalib/raspberrypi3-debian:buster-run
ENV INITSYSTEM on
ENV container docker

MAINTAINER Tai Lucas <tglucas@gmail.com>
LABEL Description="snapshot_processor" Vendor="tglucas" Version="1.0"

ENV DEBIAN_FRONTEND noninteractive
ENV DEBCONF_NONINTERACTIVE_SEEN true

RUN apt-get clean && apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    cron \
    cpp \
    curl \
    dbus \
    g++ \
    gcc \
    git \
    htop \
    jq \
    libatlas-base-dev \
    libffi-dev \
    libraspberrypi-bin \
    libjpeg-dev \
    liblapack-dev \
    libopenblas-dev \
    libsm6 \
    libxext6 \
    libxrender-dev \
    lsof \
    make \
    mediainfo \
    network-manager \
    openssh-server \
    patch \
    python3-certifi \
    python3-dbus \
    python3 \
    python3-dev \
    python3-opencv \
    python3-pil \
    python3-pip \
    python3-setuptools \
    python3-venv \
    python3-wheel \
    rsyslog \
    strace \
    systemd \
    tree \
    vim \
    vsftpd \
    wget \
    && pip3 install \
        tzupdate \
    && rm -rf /var/lib/apt/lists/*

# python3 default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

COPY . /opt/app

# setup
RUN /opt/app/app_setup.sh

# systemd masks for containers
# https://github.com/balena-io-library/base-images/blob/master/examples/INITSYSTEM/systemd/systemd.v230/Dockerfile
RUN systemctl mask \
    dev-hugepages.mount \
    sys-fs-fuse-connections.mount \
    sys-kernel-config.mount \
    display-manager.service \
    getty@.service \
    systemd-logind.service \
    systemd-remount-fs.service \
    getty.target \
    graphical.target \
    kmod-static-nodes.service \
    NetworkManager.service \
    # stop invocation of systemd-logind.service
    unattended-upgrades.service \
    # no daily upgrades
    apt-daily.service \
    apt-daily-upgrade.service

STOPSIGNAL 37
# ftp, ssh, zmq
EXPOSE 21 22 5556 5558
CMD ["/opt/app/entrypoint.sh"]
