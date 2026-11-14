#!/usr/bin/env python
import logging
import os
import sys

import umsgpack as msgpack
import zmq
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from app import FTP_NOTIFY_URL
from tailucas_pylib import APP_NAME, app_config, log
from tailucas_pylib.creds import Creds


def _file_size(file_path: str) -> int | None:
    """Size of a file, or None when it is gone or unreadable."""
    try:
        return os.path.getsize(file_path)
    except OSError:
        return None


class FtpUploadNotifier:
    """Announce completed uploads to the pipeline over a ZeroMQ ipc channel.

    The FTP server is the only party that knows when the last byte of a transfer
    has landed, so it says so rather than leaving the pipeline to infer it from
    the file system (where a finished upload and one still being written look
    the same). Notifications are advisory: the receiver still reads, size-checks
    and decodes the file, and its file system watcher still covers anything this
    channel misses.
    """

    def __init__(self, notify_url: str):
        self._notify_url = notify_url
        self._context: zmq.Context | None = None
        self._socket: zmq.Socket | None = None
        if len(notify_url) == 0:
            return
        try:
            self._context = zmq.Context()
            self._socket = self._context.socket(zmq.PUSH)
            # a notification may never hold up a transfer or a shutdown
            self._socket.setsockopt(zmq.LINGER, 0)
            self._socket.connect(notify_url)
        except zmq.ZMQError as e:
            log.warning(
                "FTP upload notifications disabled",
                extra={"notify_url": notify_url, "error": str(e)},
            )
            self._socket = None
            self._context = None

    @property
    def notify_url(self) -> str:
        return self._notify_url

    def notify(self, file_path: str, username: str | None = None, remote_ip: str | None = None) -> None:
        if self._socket is None:
            return
        notification: dict[str, object] = {
            "event": "file_received",
            "file": file_path,
            "username": username,
            "remote_ip": remote_ip,
        }
        try:
            file_stat = os.stat(file_path)
        except OSError as e:
            log.debug("Unable to stat completed upload", extra={"file": file_path, "error": str(e)})
        else:
            notification["file_size_bytes"] = file_stat.st_size
            notification["file_mtime_ns"] = file_stat.st_mtime_ns
        try:
            self._socket.send(msgpack.packb(notification), flags=zmq.NOBLOCK)
        except (zmq.ZMQError, msgpack.PackException) as e:
            log.warning(
                "Unable to send FTP upload notification",
                extra={"file": file_path, "notify_url": self._notify_url, "error": str(e)},
            )
        else:
            log.debug(
                "FTP upload notification sent",
                extra={"file": file_path, "notify_url": self._notify_url},
            )

    def close(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._context is not None:
            self._context.term()
            self._context = None


class SnapshotFTPHandler(FTPHandler):
    # configured from main(); a class attribute because that is how pyftpdlib
    # takes handler configuration (see authorizer and banner below)
    upload_notifier: FtpUploadNotifier | None = None

    def on_login(self, username):
        log.info("FTP user logged in", extra={"username": username})

    def on_file_sent(self, file):
        log.info("FTP file sent", extra={"file": file})

    def on_file_received(self, file):
        # pyftpdlib closes the file before calling this, so this is the point at
        # which the upload is complete and can be handed to the pipeline
        log.info("FTP file received", extra={"file": file, "file_size_bytes": _file_size(file)})
        if self.upload_notifier is not None:
            self.upload_notifier.notify(
                file_path=file,
                username=self.username,
                remote_ip=self.remote_ip,
            )

    def on_incomplete_file_received(self, file):
        # the client (camera) stopped mid-transfer: keep the partial file out of
        # the snapshot tree so it is never analysed or archived
        log.warning(
            "FTP received partial file. Removing...",
            extra={"file": file, "file_size_bytes": _file_size(file)},
        )
        try:
            os.remove(file)
        except OSError as e:
            log.debug("Unable to remove partial upload", extra={"file": file, "error": str(e)})

    def ftp_DELE(self, path):
        # Cameras delete the destination name before storing a snapshot. A 550
        # for a file that was never there makes them abort the upload and retry
        # in a tight loop, so treat the delete of a missing path as a success.
        if not self.fs.lexists(path):
            log.debug("FTP delete of missing file treated as success", extra={"path": path})
            self.respond("250 File removed.")
            return None
        return super().ftp_DELE(path)


def main():
    # pyftpdlib logs every single FTP command at INFO (to stderr). Attaching our
    # own handler keeps it from installing that default configuration, and the
    # level keeps per-command chatter out of the logs. Raise FTP_LOG_LEVEL to
    # INFO or DEBUG when diagnosing a client.
    pyftpdlib_logger = logging.getLogger("pyftpdlib")
    if not pyftpdlib_logger.handlers:
        pyftpdlib_handler = logging.StreamHandler(stream=sys.stderr)
        pyftpdlib_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        pyftpdlib_logger.addHandler(pyftpdlib_handler)
    ftp_log_level = os.getenv("FTP_LOG_LEVEL", "WARNING").upper()
    pyftpdlib_logger.setLevel(getattr(logging, ftp_log_level, logging.WARNING))

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
    # tell the pipeline when a transfer completes, so it does not have to infer
    # completion from the file system (see FtpUploadListener in app/__main__.py)
    upload_notifier = FtpUploadNotifier(notify_url=FTP_NOTIFY_URL)
    handler.upload_notifier = upload_notifier

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
            "upload_notify_url": upload_notifier.notify_url or None,
        },
    )
    # start ftp server
    try:
        server.serve_forever()
    finally:
        upload_notifier.close()


if __name__ == "__main__":
    main()
