---
paths:
  - "app/**"
  - "pyproject.toml"
  - "*.sh"
  - "Dockerfile"
---

# snapshot-processor Coding Standards

snapshot-processor is a **Python-only** application extending the
`tailucas/base-app` template. It is a snapshot processor for IP cameras: it
fetches images via HTTP, watches for FTP uploads, runs object detection
(YOLO locally and/or AWS Rekognition), and archives snapshots to Google
Drive. The codebase is deliberately small and readable: the core
application lives in `app/__main__.py` with a separate FTP server module.

## 1. Posture

- **Derivative-app-first.** This project is a consumer of the base-app
  template and its `tailucas_pylib` framework; prefer the pylib patterns over
  re-implementing machinery (threading, ZMQ sockets, OTEL setup, logging).
- **Boring and explicit.** Boilerplate is a feature: entrypoints, setup
  scripts, and configuration stay readable and copy-paste-able.

## 2. Python Application (`app/`)

- The app follows the `tailucas_pylib` framework: `AppThread`/`ZmqRelay`
  subclasses, ZMQ inproc transport (`URL_WORKER_APP`, `URL_WORKER_OBJECT_DETECTOR`,
  `URL_WORKER_RABBIT_PUBLISHER`, `URL_WORKER_CLOUD_STORAGE`),
  `exception_handler` for socket lifecycles, `SignalHandler` + `thread_nanny`
  + `die()`/`bye()` shutdown. OTEL provider setup happens automatically when
  `tailucas_pylib` is imported (see `observability.md`).
- Core pipeline classes in `app/__main__.py`:
  - `Snapshot` (a `ZmqRelay`): receives camera trigger events, fetches an
    image from the IP camera over HTTP, saves it, and forwards the image for
    object detection.
  - `ObjectDetector` (a `ZmqRelay`): runs YOLO (local Ultralytics model) or
    AWS Rekognition (cloud) object detection and relays results toward
    RabbitMQ; renames snapshots on person detection.
  - `UploadEventHandler`: a watchdog file-system handler that watches the
    FTP upload directory and pushes new snapshots into the pipeline.
  - `GoogleDriveUploader`/`GoogleDriveArchiver`: asynchronous upload of
    snapshots to Google Drive and periodic archival into year/month/day
    folders.
  - `app/ftp_server.py` provides the FTP upload server using `pyftpdlib`.
- Dependencies are managed with `uv` (`pyproject.toml`, `uv.lock`); depend on
  `tailucas-pylib[...]` extras, never vendored copies. Lint with
  `make lint` (`ruff format .`, `ruff check .`, `mypy app/`) before
  considering work done.
- Sentry is initialized in `main()` after credential validation; OTEL
  providers are set up by the pylib import (see `observability.md`).

## 3. Configuration & Environment

Configuration, secrets, and the `.env` → `config_interpol` flow are documented
in `config.md`. The container lifecycle, entrypoint layering, and supervised
program generation are documented in `container.md`.

## 4. Build & Run

The Makefile build graph and the Python toolchain conventions are documented
in `build.md`. The Docker build (base-image extension + ML stack install) and
run-as-user conventions are documented in `container.md`.

## 5. Cross-cutting Rules

- Logging is structured via pylib (static message + `extra` key/value fields,
  JSON output) — see `logging.md`.
- Graceful shutdown is mandatory: signal handling, resource teardown, OTEL
  flush (`die()` calls `tracing.shutdown()`).
- New integrations get a feature switch (env var or `app.conf` section) so
  the app stays runnable with zero external dependencies.