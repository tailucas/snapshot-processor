#!/usr/bin/env python
import os

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from tailucas_pylib import APP_NAME, app_config, log
from tailucas_pylib.creds import Creds


class SnapshotFTPHandler(FTPHandler):
    def on_login(self, username):
        log.info("FTP user logged in", extra={"username": username})

    def on_file_sent(self, file):
        log.info("FTP file sent", extra={"file": file})

    def on_file_received(self, file):
        log.info("FTP file received", extra={"file": file})
        # TODO: send to object detector with correct device association

    def on_incomplete_file_received(self, file):
        log.info("FTP received partial file. Removing...", extra={"file": file})
        os.remove(file)


def main():
    # Instantiate a dummy authorizer for managing 'virtual' users
    authorizer = DummyAuthorizer()

    ftp_server_port = app_config.getint("ftp", "port", fallback=21)
    ftp_banner = f"{APP_NAME} FTP."
    op_creds = Creds()
    ftp_username = op_creds.get_creds("FTP/username")
    root_dir = app_config.get("snapshots", "root_dir")

    # Define a new user having full r/w permissions and a read-only
    # anonymous user
    authorizer.add_user(
        username=ftp_username,
        password=op_creds.get_creds("FTP/password"),
        homedir=root_dir,
        perm="elradfmwMT",
    )

    # Designate the FTP handler class
    handler = SnapshotFTPHandler
    handler.authorizer = authorizer

    # Define a customized banner (string returned when client connects)
    handler.banner = ftp_banner

    # Passive (PASV) transfers: cameras open a second connection to a port in
    # this range, so it must be published by the container runtime. The
    # masquerade address is the address clients are told to reconnect to; behind
    # Docker's NAT it is the host's address, otherwise clients receive the
    # container's private address and transfers stall after login.
    passive_ports_min = int(os.getenv("FTP_PASV_MIN_PORT", "30000"))
    passive_ports_max = int(os.getenv("FTP_PASV_MAX_PORT", "30009"))
    passive_ports: range | None = None
    passive_ports_label: str | None = None
    if 0 < passive_ports_min <= passive_ports_max:
        passive_ports = range(passive_ports_min, passive_ports_max + 1)
        handler.passive_ports = passive_ports
        passive_ports_label = f"{passive_ports_min}-{passive_ports_max}"
    masquerade_address = os.getenv("FTP_MASQUERADE_ADDRESS")
    if masquerade_address:
        handler.masquerade_address = masquerade_address

    # Instantiate FTP server class and listen on 0.0.0.0:21
    address = ("", ftp_server_port)
    server = FTPServer(address, handler)

    # set a limit for connections
    server.max_cons = 16
    server.max_cons_per_ip = 5

    log.info(
        "Starting FTP server",
        extra={
            "ftp_banner": ftp_banner,
            "ftp_server_port": ftp_server_port,
            "ftp_username": ftp_username,
            "root_dir": root_dir,
            "passive_ports": passive_ports_label,
            "masquerade_address": masquerade_address,
        },
    )
    # start ftp server
    server.serve_forever()


if __name__ == "__main__":
    main()
