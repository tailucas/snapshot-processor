---
paths:
  - "app/**"
  - "pyproject.toml"
  - "Dockerfile"
---

# snapshot-processor Coding Standards

Message-driven image pipeline: fetches snapshots from IP cameras (Foscam CGI),
ingests uploads via FTP and file-system events, runs object detection
(Ultralytics YOLO locally and/or AWS Rekognition, feature-flagged), uploads
and archives images in Google Drive, and publishes device events to RabbitMQ.

## 1. Posture

- Extends base-app; depends on `tailucas-pylib[aws,creds,monitoring,mq]`.
  Follow pylib's coding and logging standards.
- Pipeline stages are threads connected by ZMQ inproc URLs; failures in one
  stage must not wedge the others (retry with backoff, then skip with a
  WARNING).

## 2. Architecture (`app/__main__.py`)

ZMQ topology (`URL_WORKER_*` constants):

- `Snapshot(ZmqRelay)` — consumes trigger events (`event.trigger.*`), fetches
  images from camera CGI endpoints (3 tries), validates image format via PIL,
  saves to the snapshot root, forwards to the object detector.
- `UploadEventHandler(watchdog)` — watches the snapshot root; maps
  directories → device events; pushes detection work to the detector socket
  and uploads to cloud storage; LRU `_path_cache` prevents duplicate
  processing.
- `ObjectDetector(ZmqRelay)` — YOLO (local) and/or Rekognition (cloud)
  person/object detection; renames snapshots (`fetch` → `detect`/`human`),
  attaches `event_detail`, forwards publisher payloads to the RabbitMQ relay.
- `GoogleDriveUploader(AppThread, GoogleDriveManager)` — uploads with checksum
  and size verification; trashes corrupted uploads and signals retry.
- `GoogleDriveArchiver(AppThread, GoogleDriveManager)` — daily archival into
  year/month/day folders; starred files keep existing parents.
- `RabbitMQRelay` / `ZMQListener` (pylib) — event publication and control
  ingestion.

Rules:

- New pipeline stages: subclass `ZmqRelay`/`AppThread`, register with the
  thread nanny, use `exception_handler` for socket lifecycle.
- Cloud/HTTP calls get bounded retries and explicit WARNING logs with
  structured fields before retrying or giving up.
- Feature flags (`object-detection`, `local-object-detection`,
  `cloud-object-detection`, `cloud-storage-management`) gate optional stages;
  always log which flag/config disabled a stage.

## 3. Auxiliary Entry Points

- `app/ftp_server.py` — pyftpdlib server writing into the snapshot root
  (uploads then flow through the watchdog path); credentials from 1Password
  (`FTP/username`, `FTP/password`).
- `app/gauth_configure.py` — interactive Google OAuth bootstrap writing the
  creds file referenced by `[gdrive] creds_file`.

## 4. Configuration & Credentials

- `app.conf` sections: `camera`, `snapshots`, `gdrive`, `rekognition`,
  `object_detection`, `human_detection`, `rabbitmq`, `ftp`, `input_type`,
  `input_location`, `output_type`, `output_location`.
- AWS credentials via pylib `aws` extra (boto session with role assumption);
  Google creds file managed by `gauth_configure`; RabbitMQ address from
  config. Never log credential contents.

## 5. Correctness Notes

- Timestamps: image file names encode the capture time; keep
  `create_snapshot_path` and the file-name parsing in
  `UploadEventHandler.on_fs_event` in sync.
- Detection results mutate file names; the `_path_cache` and `fetch`/`detect`/
  `human` path markers prevent re-processing loops — preserve these guards
  when touching the pipeline.
- Rekognition `InvalidImageFormatException` is expected noise (WARNING with
  `exc_info`), not a Sentry exception.

## 6. Testing & Lint

- Ruff per `pyproject.toml` (`select = F,E,W,B,I,UP`, line length default);
  keep new code clean and do not add new violations.
- Compile-check all entry points after changes
  (`python -m py_compile app/*.py`).
