FROM resin/rpi-raspbian:wheezy

MAINTAINER db2inst1 <db2inst1@webafrica.org.za>
LABEL Description="snapshot_processor" Vendor="db2inst1" Version="1.0"

COPY ./pipstrap.py /tmp/

RUN apt-get update && apt-get install -y --no-install-recommends \
    alsa-utils \
    apcupsd \
    ca-certificates \
    cron \
    cpp \
    curl \
    dbus \
    g++ \
    gcc \
    less \
    libffi-dev \
    libssl-dev \
    manpages \
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
    supervisor \
    taglib \
    vim \
    vsftpd \
    wget \
    # pip 8
    && python /tmp/pipstrap.py

COPY ./config/pip_freeze /tmp/
RUN pip install -r /tmp/pip_freeze
# show outdated packages since the freeze
RUN pip list --outdated

# ftp, ssh, zmq
EXPOSE 21 22 5556 5558

# sshd configuration
RUN mkdir /var/run/sshd
RUN mkdir /root/.ssh/

COPY . /app
COPY ./entrypoint.sh /

ENTRYPOINT ["/entrypoint.sh"]
