FROM debian:buster
ENV INITSYSTEM on
ENV container docker

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
    less \
    libatlas-base-dev \
    libffi-dev \
    libjpeg-dev \
    liblapack-dev \
    libopenblas-dev \
    libsm6 \
    libxext6 \
    libxrender-dev \
    lsof \
    make \
    mediainfo \
    patch \
    procps \
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
    tree \
    vim \
    vsftpd \
    wget

# python3 default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# setup
WORKDIR /opt/app
COPY requirements.txt .
COPY pylib/requirements.txt ./pylib/requirements.txt
COPY app_setup.sh .
RUN /opt/app/app_setup.sh

COPY config ./config
COPY settings.yaml .
COPY backup_auth_token.sh .
COPY healthchecks_heartbeat.sh .
COPY entrypoint.sh .
COPY pylib ./pylib
COPY pylib/pylib ./lib
COPY snapshot_processor .

STOPSIGNAL 37
# ftp, ssh, zmq
EXPOSE 21 22 5556 5558
CMD ["/opt/app/entrypoint.sh"]
