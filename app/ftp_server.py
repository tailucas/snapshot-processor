#!/usr/bin/env python
import builtins
import os

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

import os.path

# setup builtins used by pylib init
builtins.SENTRY_EXTRAS = []
from . import APP_NAME
class CredsConfig:
    ftp_user: f'opitem:"FTP" opfield:.username' = None # type: ignore
    ftp_pass: f'opitem:"FTP" opfield:.password' = None # type: ignore
# instantiate class
builtins.creds_config = CredsConfig()

from tailucas_pylib import (
    app_config,
    creds,
    log
)

class SnapshotFTPHandler(FTPHandler):

    def on_login(self, username):
        log.info(f'{username} logged in.')

    def on_file_sent(self, file):
        log.info(f'Sent {file}.')

    def on_file_received(self, file):
        log.info(f'Received {file}.')
        # TODO: send to object detector with correct device association

    def on_incomplete_file_received(self, file):
        log.info(f'Received partial file {file}. Removing...')
        os.remove(file)


def main():
    # Instantiate a dummy authorizer for managing 'virtual' users
    authorizer = DummyAuthorizer()

    ftp_server_port = app_config.getint('ftp', 'port', fallback=21)
    ftp_banner = f'{APP_NAME} FTP.'
    ftp_username = creds.ftp_user
    root_dir = app_config.get('snapshots', 'root_dir')

    # Define a new user having full r/w permissions and a read-only
    # anonymous user
    authorizer.add_user(
        username=ftp_username,
        password=creds.ftp_pass,
        homedir=root_dir,
        perm='elradfmwMT')

    # Designate the FTP handler class
    handler = SnapshotFTPHandler
    handler.authorizer = authorizer

    # Define a customized banner (string returned when client connects)
    handler.banner = ftp_banner

    # Specify a masquerade address and the range of ports to use for
    # passive connections.  Decomment in case you're behind a NAT.
    #handler.masquerade_address = '151.25.42.11'
    #handler.passive_ports = range(60000, 65535)

    # Instantiate FTP server class and listen on 0.0.0.0:21
    address = ('', ftp_server_port)
    server = FTPServer(address, handler)

    # set a limit for connections
    server.max_cons = 16
    server.max_cons_per_ip = 5

    log.info(f'Starting FTP server "{ftp_banner}" on port {ftp_server_port} with username {ftp_username} mounted at {root_dir}')
    # start ftp server
    server.serve_forever()

if __name__ == "__main__":
    main()