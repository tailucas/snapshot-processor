# snapshot-processor Observability Rules (OpenTelemetry via pylib)

snapshot-processor gets its OpenTelemetry (OTEL) support entirely from the
`tailucas-pylib` dependency (`monitoring` extra). The application code does
**not** call `setup_otel()`, import the OTEL SDK, or construct exporters —
provider setup, the experimental logs SDK, and exporter construction all live
in `tailucas_pylib`. The exporter target comes from the standard
`OTEL_EXPORTER_OTLP_*` env vars (protocol defaults to `grpc`; `http/protobuf`
is also supported). These rules document what a fork must preserve.

## 1. General Pattern (language-agnostic)

- **Identity comes from standard env vars, not code.** `service.name` comes
  from `OTEL_SERVICE_NAME`; extra resource labels from
  `OTEL_RESOURCE_ATTRIBUTES` (e.g. `deployment.environment`);
  `service.instance.id` from `APP_NAME` (pylib passes it explicitly to
  `Resource.create`). Never hardcode `service.name` as an explicit resource
  attribute: SDK merge precedence lets an explicit (or empty) value override
  the env var and degrade to `unknown_service`. In this project
  `OTEL_SERVICE_NAME`/`OTEL_RESOURCE_ATTRIBUTES` are **optional** — if unset,
  the SDK falls back to its defaults.
- **Logs ride the trace.** pylib bridges the platform logger into OTEL at
  import time (`LoggingHandler` at level `NOTSET` on the pylib logger). Log
  records emitted inside an active span automatically carry
  `trace_id`/`span_id`, giving log↔trace correlation for free.
- **Errors are span data.** `tailucas_pylib.tracing.record_exception(exc)`
  records the exception on the current span and sets ERROR status; it is
  invoked automatically by `exception_handler` and is available for explicit
  use. Telemetry must not replace existing error handling.
- **Graceful shutdown flushes.** `tailucas_pylib.tracing.shutdown()` flushes
  and shuts down every provider, guarded so exporter errors never break app
  teardown. It is called from `die()` (pylib `threads.py`), so the app's
  normal shutdown path flushes telemetry.
- **Batching needs an exit flush.** `BatchSpanProcessor`,
  `BatchLogRecordProcessor`, and `PeriodicExportingMetricReader` all buffer;
  without the shutdown flush, short runs export nothing. The pylib `die()`
  path provides this.

## 2. Python-specific (`app/`, opentelemetry-python)

- **OTEL bootstrap lives in `tailucas_pylib`.** `app/__main__.py` does not
  call `setup_otel()` or import the SDK directly; provider setup, the
  experimental logs SDK (`opentelemetry.sdk._logs`,
  `...proto.grpc._log_exporter`), and exporter construction all live in the
  external `tailucas_pylib` dependency. The exporter interface to reference
  there is `LogRecordExporter`; `LogExporter` is a deprecated subclass and
  will fail type checks. Exporters are built with default constructors so
  endpoint/protocol come from the standard `OTEL_EXPORTER_OTLP_*` env vars.
- **`Resource.create()` precedence (SDK ≥1.44):** explicitly passed
  attributes win over `OTEL_SERVICE_NAME`. pylib passes only
  `service.instance.id`; the SDK's env detector supplies `service.name`.
- **Log bridge goes on the pylib logger** (`logging.getLogger(APP_NAME)`)
  with level `NOTSET`; the logger's own level governs. Remember
  `LOG_LEVEL` gates bridged records: with the level unset, effective
  `WARNING` silently drops `INFO` demo logs.
- **Keep mypy clean across the protocol branch:** pylib annotates the
  selected exporters with the SDK interfaces (`SpanExporter`, `MetricExporter`,
  `LogRecordExporter`) and aliases the per-protocol imports
  (`GrpcOTLPSpanExporter`/`HttpOTLPSpanExporter`).

## 3. Metrics in this project

The application does **not** create OTEL instruments. Its operational metrics
are:

- **Sentry metrics** (`sentry_sdk.metrics.distribution`): `capture_time`,
  `snapshot_handoff_time`, `detect_time`, `detect_confidence` — emitted from
  `Snapshot` and `ObjectDetector` in `app/__main__.py`.
- **CloudWatch counters** (`tailucas_pylib.aws.metrics.post_count_metric`):
  `Errors` and similar counters posted to the `automation` namespace.

The pylib `monitoring` extra still wires OTEL metric readers
(`PeriodicExportingMetricReader`, 60s default interval) so OTEL metrics
*can* be added later without changing the bootstrap. If OTEL instruments are
introduced, follow the pylib conventions: create tracers/meters/instruments
after the providers are registered (thread constructors run after import),
and prefer a short export interval (~10s) in demo code so data appears
promptly.

## 4. Verification Pattern

- Run `ruff check .` and `mypy app/` at the project baseline.
- Verify the pylib OTEL bootstrap by setting `OTEL_EXPORTER_OTLP_ENDPOINT`
  (or the standard `OTEL_EXPORTER_OTLP_*` vars) and confirming log records
  carry nonzero `trace_id`/`span_id` at a local stub collector.
- `OTEL_SDK_DISABLED=true` suppresses export while spans still record in
  memory — assert on what leaves the process, never on `isRecording()`.