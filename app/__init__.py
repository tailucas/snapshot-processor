"""Snapshot processor application package."""

import os

# The FTP server runs as a separate program from the pipeline, so it announces
# completed uploads over this ZeroMQ ipc endpoint instead of leaving the
# receiver to infer completion from the file system (see app/ftp_server.py and
# FtpUploadListener in app/__main__.py). An empty value disables the channel on
# both sides, leaving the file system watcher as the only upload trigger.
FTP_NOTIFY_URL = os.getenv("FTP_NOTIFY_URL", "ipc:///tmp/snapshot-processor-ftp.sock")
