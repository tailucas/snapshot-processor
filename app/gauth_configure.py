#!/usr/bin/env python
import logging
import os.path

from pydrive2.auth import GoogleAuth

from tailucas_pylib import app_config, log


def trigger_oauth(auth: GoogleAuth, creds_file: str):
    auth.LocalWebserverAuth(bind_addr="0.0.0.0", launch_browser=False)
    auth.SaveCredentialsFile(creds_file)
    log.info(f"Saved Google credentials to {creds_file}")


def main():
    creds_file = app_config.get("gdrive", "creds_file")
    if "~" in creds_file:
        creds_file = os.path.expanduser(creds_file)
    else:
        creds_file = os.path.abspath(creds_file)

    creds_missing = not os.path.exists(creds_file) or os.path.getsize(creds_file) == 0

    auth = GoogleAuth()
    if creds_missing:
        log.info(f"Google credentials missing or empty in [{creds_file}]. Starting interactive OAuth setup...")
        trigger_oauth(auth, creds_file)
    else:
        log.debug(f"Loading Google credentials from [{creds_file}]...")
        auth.LoadCredentialsFile(creds_file)
        if auth.credentials is None:
            log.warning(f"Credentials file [{creds_file}] exists but contains no valid credentials. Starting interactive OAuth setup...")
            trigger_oauth(auth, creds_file)
        elif auth.access_token_expired:
            log.info("Access token expired, refreshing...")
            auth.Refresh()
            auth.SaveCredentialsFile(creds_file)
            log.info(f"Refreshed and saved Google credentials to {creds_file}")
        else:
            auth.Authorize()
            log.info(f"Google credentials in [{creds_file}] are valid.")


if __name__ == "__main__":
    main()