---
paths:
  - "Dockerfile"
  - "docker-compose.yml"
  - "*.sh"
  - "config/cron/**"
---

# snapshot-processor Container & Process Management

snapshot-processor is a **Docker image extending `tailucas/base-app:latest`**:
a single Ubuntu container running **supervisord** as the process manager, with
a layered entrypoint/setup convention inherited from the template. This file
documents the container lifecycle and the **override seam** that lets a fork
add behaviour without editing the base image's core scripts.

## 1. Build (`Dockerfile`)

The `Dockerfile` is a **single stage** that extends
`tailucas/base-app:latest`:

- `ENV UV_NO_DEFAULT_GROUPS=1` so `uv sync` installs main dependencies only.
- `USER root` for locale generation (LANG/LANGUAGE args) and config writes.
- Copies the fork's scripts (`backup_auth_token.sh`), config and cron files;
  removes the inherited `config/cron/base_job`.
- Runs the inherited `app_setup.sh` (registers crons).
- Installs the object-detection stack inline with `uv pip install`:
  `ultralytics`, `opencv-python-headless`,
  `torch`/`torchvision` (CPU index), and supporting ML libraries.
- Switches to `USER app` before running the inherited `python_setup.sh`.
- Copies the application source (`app/__main__.py`, `app/ftp_server.py`,
  `app/gauth_configure.py`) and `settings.yaml`.
- `CMD` is the inherited `/opt/app/entrypoint.sh`.

Keep the runtime image free of build toolchains; the base image already owns
the Python toolchain. Anything that only produces an artifact belongs in the
base image or in the Dockerfile build context, not in the runtime layer.

## 2. Entrypoint layering (the override seam)

The container's `CMD` runs `/opt/app/entrypoint.sh` (inherited), which sources
the template scripts in order before `exec supervisord`:

```sh
. /opt/app/base_entrypoint.sh
. /opt/app/app_entrypoint.sh
exec env supervisord -n -c /opt/app/supervisord.conf
```

- **`base_entrypoint.sh`** (inherited) is the **template's contract**: it
  interpolates config, copies `config/supervisord.conf`, appends the cron
  program, exports `cron.env`, and appends the supervised program blocks for
  each enabled runtime.
- **`app_entrypoint.sh`** is the **fork's override point**. In this project it
  is a meaningful, non-empty script:
  - writes the Google OAuth client secret from 1Password to
    `client_secrets.json`,
  - restores/refreshes the Google auth token via `backup_auth_token.sh`,
  - creates the FTP root and per-camera upload directories,
  - appends a `[program:ftp]` block to the active supervisord config when
    `RUN_FTP_SERVER=true`,
  - runs `gauth_configure` (interactive Google OAuth flow) if no token file
    exists.

The same split applies at build time: **`app_setup.sh`** (inherited from the
base image) registers the cron files; the fork does not ship its own
`base_setup.sh`.

**Rule:** a fork must not edit the base image's `base_entrypoint.sh`/
`base_setup.sh`; it should put its own logic in `app_entrypoint.sh`/
`app_setup.sh` so upstream template changes can be merged without conflict.
If a fork needs its own setup script, name it distinctly (e.g.
`app_setup.sh`) and invoke it from the Dockerfile.

## 3. supervisord program generation via feature flags

The active `[program:*]` sections are generated at container start: the
inherited `base_entrypoint.sh` appends the template programs (each guarded by
an environment feature flag), and `app_entrypoint.sh` appends the fork's FTP
program.

| Flag | Program | Command | Source |
|---|---|---|---|
| `NO_PYTHON_APP` (unset) | `app` | `uv run --frozen --no-sync app` | base image |
| `NO_CRON` (unset) | `cron` | `/usr/sbin/cron -f -L 4` | base image |
| `RUN_FTP_SERVER=true` | `ftp` | `uv run --frozen --no-sync ftp` | `app_entrypoint.sh` |

The base-image programs set `priority`, `directory=/opt/app/`, `user=app`,
`autorestart=unexpected`, `stopwaitsecs=30`, and route stdout/stderr to
`/dev/stdout`/`/dev/stderr` with `stdout_events_enabled=true`.

The project's `[program:ftp]` block currently sets only `command`,
`directory`, `user`, and `autorestart=unexpected`. When extending it, follow
the base-image conventions (`stopwaitsecs`, stdout/stderr routing).

**Rule:** a fork adds its own supervised program by appending a `[program:*]`
block in `app_entrypoint.sh` (guarded by a feature flag), never by editing a
repo-local supervisord config that the base image overwrites. Every
integration gets a feature switch so the app stays runnable with zero
external dependencies.

## 4. Run-as-user convention

- The app runs as a **no-password user `app` with UID/GID `999`** (created in
  the base image with `useradd -r -u 999 -g 999 app`).
- The Dockerfile switches to `USER app` **before** running `python_setup.sh`,
  because `uv` does not infer the target user from the environment —
  toolchain installs that write to `$HOME` must run as `app`.
- The host-side `data/` directory is owned by `USER_ID:GROUP_ID` (default
  `999:999`) so the in-container `app` user can write to it; override with
  `make datadir USER_ID=... GROUP_ID=...`.

## 5. Toolchain installation

The Python toolchain is managed by the base image (`uv`, `python_setup.sh`).
The fork installs the **object-detection stack** inline in the Dockerfile via
`uv pip install` (ultralytics, OpenCV headless, PyTorch CPU from the dedicated
index, numpy, matplotlib, polars, pillow, pyyaml, requests, scipy,
`ultralytics-thop`). Keep fork-specific installs in the Dockerfile or in
`app_setup.sh`, not in the base image's scripts.

## 6. Cron orchestration

- The inherited `app_setup.sh` concatenates the fork's `config/cron/*` files
  into a single crontab registered for the `app` user (`crontab -u app`).
  This project ships `backup_auth_token` (Google token backup) and
  `cleanup_snapshots` (old snapshot cleanup); the base `base_job` is removed
  in the Dockerfile.
- `base_entrypoint.sh` exports the full environment to `/opt/app/cron.env`
  (`printenv`), which cron jobs source before running (e.g.
  `backup_auth_token.sh`).
- The cron program itself is appended by `base_entrypoint.sh` unless
  `NO_CRON=true`.

**Rule:** add a new scheduled job by dropping a crontab file into
`config/cron/` and sourcing `cron.env` inside the script it invokes.

## 7. Graceful shutdown

- `docker-compose.yml` sets `stop_grace_period: 45s`.
- Each supervised program sets `autorestart=unexpected`; base-image programs
  set `stopwaitsecs=30`.
- The Python app handles signals via `SignalHandler` + `thread_nanny` and
  tears down ZMQ sockets in `die()`/`zmq_term()`; `die()` also flushes OTEL
  via `tracing.shutdown()` (see `observability.md`).

Graceful shutdown is mandatory — signal handling and resource teardown must
be wired so a container stop does not drop in-flight work or telemetry.