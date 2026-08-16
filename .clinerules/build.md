---
paths:
  - "Makefile"
  - "pyproject.toml"
  - "uv.lock"
  - "Dockerfile"
  - "*_setup.sh"
---

# snapshot-processor Build System (Python Toolchain)

snapshot-processor is a **Python-only** application built on the
`tailucas/base-app:latest` base image. The `Makefile` is a **build graph**,
not a list of commands: artifacts are modeled as real file targets so repeated
invocations skip work that is already up to date. This file documents the
conventions a fork must preserve when extending the application.

## 1. Makefile conventions

- **Self-documenting.** `make` with no arguments (or `make help`) lists every
  target with a one-line description extracted from the `##` comment on the
  same line. Add a `##` comment to every new target.
- **Incremental builds via real file targets.** Generated artifacts are file
  targets that rebuild only when missing or when their inputs change:
  - `.env` depends on `base.env` and `docker-compose.yml`, and is
    sanity-checked with a minimum line count.
  - `.venv` depends on `pyproject.toml` and `uv.lock`.
  - `data/` depends on nothing — it is created (and chowned to
    `USER_ID:GROUP_ID`) only if missing.
- **Failure safety.** `.DELETE_ON_ERROR` deletes the partial output of a
  failed recipe, so a stale artifact can never look up-to-date.
- **Ordered prerequisites.** `make run` and `make rund` resolve `data/` →
  `build` → `.env` in order before starting the container. Prerequisite order
  is only guaranteed for serial builds — **do not use `make -j`**.
- **Inlined preconditions.** Tool checks (`docker`, `docker compose`, `uv`,
  `devcontainer`) and environment checks (a running 1Password Connect
  container) fail fast with clear messages before any work is done.
- **Host vs. container awareness.** `make dev`, `dev-build`, and `dev-up`
  manage the VS Code dev container and are guarded by `make check`, which
  refuses to run *inside* the container. All other targets work identically on
  the host or in the dev container via Docker-outside-of-Docker.
- **Parameterized ownership.** `data/` is owned by `USER_ID:GROUP_ID`
  (default `999:999`); override per invocation, e.g.
  `make datadir USER_ID=1000 GROUP_ID=1000`.

## 2. Python (`pyproject.toml`, `uv`)

- Dependencies are managed with **uv**; `pyproject.toml` + `uv.lock` are the
  source of truth. The runtime depends on `tailucas-pylib[...]` extras plus
  project-specific packages (`awscli`, `cachetools`, `pydrive2`,
  `pyftpdlib`, `watchdog`) — never vendor copies of shared-library code.
- **Dependency groups** separate concerns:
  - `dev`: `ruff`, `mypy` (linting/type checking).
  - `coding`: `pillow`, `torch`, `torchvision`, `ultralytics` — the object
    detection stack (CPU PyTorch index in this project).
  The production image sets `UV_NO_DEFAULT_GROUPS=1` so `uv sync` installs
  main dependencies only; `coding` packages are installed separately in the
  Dockerfile.
- **Entry-point scripts** (`[project.scripts]`) expose the pylib CLI tools
  plus project tools: `gauth_configure`, `aws_configure`,
  `config_interpol`, `cred_tool`, `yaml_interpol`. These are invoked via
  `uv run --frozen --no-sync <tool>` inside the container.
- Lint with `make lint` (`ruff format .` + `ruff check .` + `mypy app/`)
  before considering work done.

## 3. Container build (`Dockerfile`)

The `Dockerfile` extends `tailucas/base-app:latest` and does **not** build
language toolchains itself:

- Sets `UV_NO_DEFAULT_GROUPS=1` so the production image installs main
  dependencies only.
- Switches to `USER root` for locale generation and config writes, then back
  to `USER app` before `python_setup.sh` (inherited from the base image)
  and the machine-learning dependency install.
- Installs the `coding` stack inline via `uv pip install`:
  `ultralytics`, `opencv-python-headless`, `torch`/`torchvision` from the
  CPU-only PyTorch index, and supporting libraries (`numpy`, `matplotlib`,
  `polars`, `pillow`, `pyyaml`, `requests`, `scipy`, `ultralytics-thop`).
- Copies the application source (`app/`), scripts, `settings.yaml`, and
  cron files; removes the inherited `config/cron/base_job`.
- The final image runs `/opt/app/entrypoint.sh` (inherited base entrypoint).

Keep toolchain installs out of the repo where the base image already owns
them; put fork-specific installs in the Dockerfile (as done for the ML stack)
or in `app_setup.sh`.

## 4. Setup scripts

Toolchain setup scripts belong to the **base image**, not this project; the
fork must not expect to maintain them here. The fork ships
`dot_env_setup.sh` (`.env` generation from 1Password) and `app_entrypoint.sh`
(runtime override that appends the FTP supervised program and drives Google
OAuth setup). The inherited `python_setup.sh` is invoked from the Dockerfile.
