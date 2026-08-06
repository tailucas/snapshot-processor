#!/usr/bin/env python
import os

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from tailucas_pylib import APP_NAME, app_config, creds, log


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
    ftp_username = creds.get_creds("FTP/username")
    root_dir = app_config.get("snapshots", "root_dir")

    # Define a new user having full r/w permissions and a read-only
    # anonymous user
    authorizer.add_user(
        username=ftp_username,
        password=creds.get_creds("FTP/password"),
        homedir=root_dir,
        perm="elradfmwMT",
    )

    # Designate the FTP handler class
    handler = SnapshotFTPHandler
    handler.authorizer = authorizer

    # Define a customized banner (string returned when client connects)
    handler.banner = ftp_banner

    # Specify a masquerade address and the range of ports to use for
    # passive connections.  Decomment in case you're behind a NAT.
    # handler.masquerade_address = '151.25.42.11'
    # handler.passive_ports = range(60000, 65535)

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
        },
    )
    # start ftp server
    server.serve_forever()


if __name__ == "__main__":
    main()
