---
paths:
  - "app/**"
---

# Structured Logging Standard (snapshot-processor)

All logging is **structured**: a static event message plus an `extra` dict of
`snake_case` fields. Interpolated log messages (f-strings, `%`-args,
`.format()`, concatenation) are prohibited.

## The Logger

```python
from tailucas_pylib import log
```

JSON output (python-json-logger) configured by `tailucas_pylib`: stdout below
ERROR, stderr from ERROR up; `SYSLOG_ADDRESS` routes INFO+ to syslog when set.
Every `extra` key becomes a top-level JSON field.

## The Pattern

```python
log.info(
    "Fetching image data from IP camera",
    extra={"device_label": device_label, "camera_url": camera_config.url},
)
log.warning(
    "Google Drive problem uploading file",
    extra={"file_path": file_path, "error": str(e)},
)
log.info(
    "YOLO labels found",
    extra={
        "label_count": len(labels),
        "device_label": device_label,
        "labels": labels,
        "snapshot_path": snapshot_path,
    },
)
```

Never:

```python
log.info(f"Saving {device_label} image data to {output_filename}...")
log.exception(f"Problem saving image to {output_filename}: {e!s}")
log.info(message.format("RabbitMQ control"))
```

## Rules

1. **Static message; data in `extra`** with `snake_case` keys and
   JSON-friendly values (`str(...)`, lists of tuples for label sets, etc.).
2. **Pipeline stage events** read as verbs: "Fetching image data...",
   "Sending image for object detection", "Uploaded file to Google Drive
   folder", "Archiving file", "Renaming snapshot after person detection".
   Keep stage identity in fields (`image_source`, `snapshot_path`,
   `device_label`).
3. **Exceptions:** `log.exception("Static message", extra={...})` for fatal
   stage errors; `log.warning(..., exc_info=True)` for expected/recoverable
   ones (e.g. Rekognition image format errors). Include `"error": str(e)`
   when no traceback is attached.
4. **Retry visibility:** log a WARNING with attempt context before each retry
   ("Problem getting image. Retrying...", extra with `camera_url`, `error`)
   and a final WARNING when giving up (extra with `tries`).
5. **Cloud storage integrity:** upload verification logs carry
   `file_base_name`, `upload_file_id`, expected/uploaded sizes and checksums
   as separate fields.
6. **Never log credentials** (Google tokens, 1Password values); the FTP
   handler logs usernames but never passwords.
7. **Shutdown sequence** uses explicit static messages per component
   ("Shutting down RabbitMQ control listener...", "Shutting down RabbitMQ
   relay...", "Shutting down application threads...").
8. **Levels.** DEBUG for drive/inference internals; INFO for pipeline stage
   transitions and detection results; WARNING for retries, duplicates,
   corrupted files; ERROR for malformed payloads and missing configuration.
