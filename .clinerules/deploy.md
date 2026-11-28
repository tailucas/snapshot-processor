# Deployment — building and shipping `snapshot-processor` with GitHub Actions

**Status: repo changes applied 2026-11-27, NOT committed, NOT yet deployable.**
The live stack on `acer` was deliberately left untouched (§7 closes the ordering
dependency before the first deploy).

Reference implementation: `tailucas/simple-app` (`.clinerules/github-actions.md`).
Runner facts: home-lab `.clinerules/github-runner.md`, `docs/ci-deploy.md`.
This file covers only what is different for **this** app.

## 1. What this application is

The camera **ingest and object-detection** service:

* a **pyftpdlib FTP server** — cameras upload snapshots to it;
* an **Ultralytics/torch CPU** object-detection pipeline that decides whether a
  frame contains anything interesting;
* **pydrive2** upload to Google Drive, plus a RabbitMQ notification;
* two cron jobs: `backup_auth_token` and `cleanup_snapshots`.

It is the heaviest and the most state-bearing of the three, and it is the only
one that holds a credential **file** you cannot regenerate.

## 2. Runtime contract

| Requirement | Detail |
|---|---|
| **Google OAuth token** | `data/snapshot_processor_creds` — see §3. **Irreplaceable.** |
| **1Password Connect** | Runtime dependency: `app/ftp_server.py` uses `Creds()`. Live it arrives via compose `secrets:` → `/run/secrets/*`; `.env` has **no `OP_*`**. |
| **FTP data ports** | `21` plus the published passive range `30000-30009`. These must stay in step with `FTP_PASV_MIN_PORT`/`FTP_PASV_MAX_PORT` in `.env`; drifting one without the other gives a server that authenticates and then hangs on `LIST`. |
| **`settings.yaml`** | Copied into the image; contains the `[gdrive]` section whose `creds_file` points at the token above. |
| **`config/` + `config/cron/`** | `backup_auth_token` and `cleanup_snapshots` jobs are registered at build time via `app_setup.sh`. |
| **Locale** | `LANG`/`LANGUAGE`/`LC_ALL` in `.env`. |

The build needs **no** secret. But note §5.3: it is **not reproducible from
`uv.lock` alone**.

## 3. `./data/` — what it is, and the rules

`/home/tai/snapshot-processor/data` (bind-mounted at `/data`, **1.1 GB** on
2026-11-27):

| Path | What it is | Replaceable? |
|---|---|---|
| `snapshot_processor_creds` | **Google Drive OAuth token.** Written by `gauth_configure.py` after an interactive authorisation. | **No.** Without it the container attempts interactive OAuth at start-up, which cannot succeed headless — the app then serves nothing. |
| `ftp/uploads/` | Camera uploads awaiting detection/upload. The bulk of the 1.1 GB. | Yes — but see below. |
| `detect/` | Detection outputs. | Yes. |
| `fetch_*.jpg` | Frames fetched for inspection. | Yes. |
| `cron-std{out,err}---supervisor-*.log`, `ftp-stderr---supervisor-*.log` | supervisord-captured output. | Yes. |

Rules:

* **Never recreate or clear `data/`.** Losing `snapshot_processor_creds` is the
  one failure here that no rebuild fixes.
* **Back up the token file separately** from the bulk — it is a credential and it
  must not travel to a CI runner. `.dockerignore` excludes `data/` wholesale.
* The 1.1 GB is **why `.dockerignore` matters most in this repo**: without it
  every CI build uploads the whole tree as build context.
* `cleanup_snapshots` prunes uploads, so the size is self-managing — but it also
  means "the directory looks smaller after a deploy" is expected, not a bug.
* Ownership must stay uid 999 (`app`); a root-owned token file makes Drive upload
  fail with a permission error that reads like an auth error.

## 4. Current deployment (verified 2026-11-27, read-only)

| Fact | Value |
|---|---|
| Host / dir | `acer`, `/home/tai/snapshot-processor` |
| Container | `snapshot-processor-app-1`, image `tailucas/snapshot-processor`, up, `restarts=0` |
| Ports | 21 (FTP), 30000-30009 (passive), 8080, 8090 |
| Image ref | `tailucas/snapshot-processor:latest` (`lab_image_snapshot_processor`) |
| Catalog | `ansible/vars/container_catalog.yml` → `snapshot-processor` |
| Secrets live | `/run/secrets/op_connect_token`, `/run/secrets/op_vault`; no `OP_*` in `.env` |
| `data/` | 1.1 GB, dominated by `ftp/uploads/` |
| Repo | `tailucas/snapshot-processor`, branch **`master`** |

## 5. What is specific to this project

1. **Branch is `master`, not `main`.**
2. **`hostname:` was a copy-paste bug**: `hostname: ${DEVICE_NAME:-base-app}`.
   Fixed to `snapshot-processor`. It only shows in `docker inspect`/syslog, so it
   never caused a visible failure — which is why it survived.
3. **The build is heavy and only partly reproducible.**
   `python_setup.sh` installs from `uv.lock`, but the Dockerfile then runs
   `uv pip install ultralytics opencv-python-headless torch torchvision numpy
   matplotlib polars pyyaml pillow psutil requests scipy ultralytics-thop`
   **outside the lock file**. Expect:
   * a slow, multi-GB CI build (torch CPU wheel), and
   * dependency drift that `uv.lock` does not pin.
   Pinning those is a separate, deliberate change; do not "fix" it in a CI commit.
4. **`uv sync --reinstall-package app` is load-bearing.** Both this project and
   `tailucas_pylib` ship a `config_interpol` console script; whichever installs
   last wins. The reinstall keeps this project's wrapper, which stops pylib's
   import-time log records from landing in the stdout that
   `base_entrypoint.sh` redirects into `app.conf`. Removing it breaks config
   interpolation in a way that looks like a missing environment variable.
5. **`ruff` and `mypy` are both clean** on `app/` (5 source files), so CI gates on
   both — cheap compared with the image build.
6. **No Java**, so no Maven-`PATH` concern. `.python-version` is already `3.14`.

## 6. Verifying a deploy

```bash
cid=$(docker compose ps -q app)
docker logs "$cid" 2>&1 | grep "success: app entered RUNNING state"
sleep 45                 # the ML stack is slow to initialise
! docker logs "$cid" 2>&1 | grep -q "exited:"
docker inspect "$cid" --format '{{.State.Status}} restarts={{.RestartCount}}'
test -s /home/tai/snapshot-processor/data/snapshot_processor_creds   # token intact
```

Why not something better:

* The app's own log lines do not reach `docker logs`; there is no `Startup
  complete.` landmark in this project to grep for.
* Port 21 is accepted by `docker-proxy` but returned **no FTP greeting** on
  2026-11-27 despite `RUN_FTP_SERVER=true`, so a TCP connect proves nothing. If
  that is investigated later, an FTP `220` banner would be the better assertion —
  but do not build a gate on today's behaviour.
* `ss -ltn` is worthless here for the same reason.

The genuinely meaningful checks are outside the deploy job: a camera upload
arriving, a detection appearing in `data/detect/`, or a Drive upload landing.

## 7. Before the first deploy — ordering matters

The repo compose no longer declares `secrets:`, but the **host was not touched**:

* `/home/tai/snapshot-processor/docker-compose.yml` still has `secrets:` and
  relies on the operator's shell;
* `/home/tai/snapshot-processor/.env` still has **no `OP_*`**.

Before deploying, in this order:

1. Confirm `data/snapshot_processor_creds` exists and is non-empty, and take a
   copy **outside** `data/` (it is the only unrecoverable thing here).
2. Add `OP_CONNECT_HOST=http://host.docker.internal:8082`, `OP_CONNECT_TOKEN`,
   `OP_VAULT` to the host `.env` (`chmod 600`). The token is already inside the
   running container: `docker exec <cid> cat /run/secrets/op_connect_token`.
3. Mirror the `secrets:` removal on the host compose.
4. Prove it: `env -u OP_CONNECT_TOKEN -u OP_VAULT docker compose up -d --force-recreate`,
   then confirm the container stays up and `data/` still holds the token file.
5. Only then grant the repo to `acer-deploy`.

## 8. Related documents

| Document | Why |
|---|---|
| `tailucas/simple-app` → `.clinerules/github-actions.md` | reference implementation and environment/1Password rules |
| `tailucas/net-tool` → `.clinerules/deploy.md` | the supervisord-based verification pattern this file reuses |
| home-lab → `docs/ci-deploy.md` | adoption checklist and ownership boundary |
| home-lab → `.clinerules/github-runner.md` | runner names, groups, labels, silent-failure pitfalls |
| home-lab → `.clinerules/observability.md` | why "no metrics appear" is a wiring question, not a deploy failure |

