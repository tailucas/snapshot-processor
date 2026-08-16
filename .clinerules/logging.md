---
paths:
  - "app/**"
  - "pyproject.toml"
---

# Structured Logging Standard (snapshot-processor)

All code in this project logs in **structured** style: a static event
message plus key/value fields. Interpolation (f-strings, `%`/`{}`
placeholders, concatenation) should be avoided and used only for descriptive
scalars with no query value of their own (e.g. a count embedded for
readability). Never interpolate secrets or untrusted data into a message.

## Python (`app/`)

The logger comes from the shared library:

```python
from tailucas_pylib import log

log.info("Sink socket started", extra={"zmq_url": self._zmq_url})
log.info(
    "Startup complete",
    extra={"env_var_count": len(env_vars), "env_vars": env_vars},
)
```

Rules:

1. Static message describing the event; all data in `extra` as a dict with
   `snake_case` keys.
2. Prefer a static message with data in `extra`. Interpolation is acceptable
   only for a descriptive scalar (e.g. a count or an identifier already
   present elsewhere in the record). Never interpolate secrets.
3. Exceptions: `log.exception("Static message", extra={...})` or
   `exc_info=True`.
4. Never log secrets (use masked hints or `*_set` booleans).
5. Output is JSON (python-json-logger) via pylib: stdout below ERROR, stderr
   from ERROR up; `SYSLOG_ADDRESS` routes INFO+ to syslog when configured.

## Levels

Choose the level by the *consequence* of the event, not by how interesting it
is. Default to the lowest level that still tells the story, and follow
**one event = one line**: a single logical event produces a single structured
record with all context in its fields.

| Level | Use |
|---|---|
| DEBUG | The default for routine, per-message/per-iteration detail: internal state, field values, step-by-step progress. Safe to drop in production. |
| INFO | An action of consequence to an upstream or downstream dependency — e.g. taking an action, triggering a mutation, a state transition, or a lifecycle boundary (startup/shutdown). Something an operator would want to see in normal operation. |
| WARNING | A non-error variation of normal logic, or a situation where the correct action is ambiguous: retries, fallbacks, degraded mode, unexpected-but-handled input. Execution continues. |
| ERROR | An exception or condition where normal execution cannot continue — e.g. returning after catching an exception, or abandoning a unit of work. |
| CRITICAL | The process is about to exit or is in an unrecoverable app-level state. Reserved for fatal failures. |

### Exception handling

- **Log once, at the boundary.** Do not log-and-rethrow the same exception at
  every layer. Log where the error is handled (or where execution stops), and
  let the telemetry carry the rest of the context.
- **Non-recoverable errors must be captured in the trace.** For every ERROR
  where execution cannot continue, record the exception on the active OTEL
  span — `record_exception(exc)` (from `tailucas_pylib.tracing`, invoked
  automatically by `exception_handler` and available for explicit use) — so
  the failure is queryable in the trace, not only in the log.
- **Recoverable problems are WARNING, not ERROR.** A retry that succeeds is
  a WARNING (or DEBUG if routine); escalate to ERROR only when the work is
  abandoned.