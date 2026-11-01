#!/usr/bin/env python
"""Safe wrapper around ``tailucas_pylib.tools.config_interpol``.

The inherited ``base_entrypoint.sh`` pipes the tool's stdout straight into the
file it is generating:

    uv run --frozen --no-sync config_interpol < config/app.conf > /opt/app/app.conf

``tailucas_pylib`` attaches its JSON log handler to ``sys.stdout`` at import time
(``logging.StreamHandler(stream=sys.stdout)``) and logs while it is imported, so
with a verbose ``LOG_LEVEL`` (e.g. ``debug``) those log records are prepended to
the config file being written. The library then re-reads ``/opt/app/app.conf`` on
import (``app_config.read``) and dies with
``configparser.MissingSectionHeaderError: File contains no section headers``,
which aborts the entrypoint chain under ``set -e`` and the container never starts.

Importing the library with ``sys.stdout`` temporarily pointed at stderr keeps the
caller's stdout clean, so the redirected file contains only interpolation output.
Nothing else is reimplemented: the actual work is delegated to pylib.
"""

import sys


def main() -> None:
    caller_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        from tailucas_pylib.tools.config_interpol import main as interpolate
    finally:
        sys.stdout = caller_stdout
    interpolate()


if __name__ == "__main__":
    main()
