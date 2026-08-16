---
paths:
  - "app/**"
  - "pyproject.toml"
---

# snapshot-processor Messaging Patterns (ZeroMQ, RabbitMQ)

snapshot-processor uses two messaging technologies for two different jobs.
They are **not interchangeable** and are documented in separate sections
below. The guiding rule is to match the transport to the boundary it crosses:

- **ZeroMQ** moves data *inside* a process, between threads, with no broker.
- **RabbitMQ** moves data *between* processes/services, through a broker.

The Python implementations live in `app/__main__.py` and the shared
`tailucas_pylib` dependency (`zmq.py`, `app.py`, `rabbit.py`). The `mq` extra
in `pyproject.toml` (via `tailucas-pylib[mq]`) pulls `pika` (RabbitMQ) and
`pyzmq` (ZeroMQ).

---

## 1. ZeroMQ — in-process pipeline, relay, and RPC

ZeroMQ is the default inter-thread transport in snapshot-processor. It is a
**brokerless socket library**, not a message broker: there is no server, no
queue persistence, and no delivery guarantee beyond what the socket pattern
itself provides. In this project it is used over the `inproc://` transport,
which means messages never leave the process — they are handed between threads
through memory, giving "lockless" communication with no shared mutable state.

### The pipeline pattern (PUSH/PULL)

The canonical example in `app/__main__.py` is a multi-stage pipeline:

```
Snapshot (PUSH) ──► ObjectDetector (PULL→PUSH) ──► RabbitMQRelay (PULL)
UploadEventHandler (PUSH) ──► GoogleDriveUploader (PULL)
```

- A **producer** (`Snapshot`, `UploadEventHandler`) binds/connects a `PUSH`
  socket and sends messages downstream.
- A **consumer** (`ObjectDetector`, `GoogleDriveUploader`) binds a `PULL`
  socket and blocks in `recv_pyobj()`.
- PUSH/PULL is **one-way and load-balancing**: with multiple PULL peers,
  ZeroMQ round-robins messages among them. It is fire-and-forget — there is no
  acknowledgement, so a stage that must not lose work should not rely on PUSH
  alone.

The socket lifecycle is centralized in `exception_handler` (a context
manager): it decides **bind vs. connect** by socket type (`PULL`/`PUB`/`REP`
bind; `PUSH`/`REQ` connect), creates the socket, and guarantees teardown on
exit or error. `zmq_term()` shuts the shared context down at process exit.

### The relay pattern (`ZmqRelay`)

A relay is a stage that **receives on one socket and forwards on another**,
optionally transforming the message in between. `tailucas_pylib.app.ZmqRelay`
encodes this as a base class: a source socket (PULL) plus a sink socket
(PUSH), with a `process_message(sink_socket)` hook that subclasses override.
`Snapshot` and `ObjectDetector` in `app/__main__.py` are the concrete
examples — `Snapshot` receives a camera trigger, fetches the image, and
forwards it for detection; `ObjectDetector` receives an image, runs
detection, and forwards the result toward RabbitMQ.

This is the "processing relay" pattern: a relay is a **transform stage**, not
a router. It is the natural place to enrich, filter, or re-encode a message
as it moves through a pipeline. Generalize it by subclassing `ZmqRelay` and
implementing only `process_message`; the base class owns the socket plumbing
and the run loop.

### The RPC pattern (REQ/REP)

ZeroMQ also provides request/reply via `REQ`/`REP` sockets, exposed in
`tailucas_pylib.app.ZmqWorker`. A worker binds a `REP` socket, receives a
request object, computes a response, and sends it back; a client uses a `REQ`
socket. This is a **synchronous, one-request-one-reply** contract — the
client blocks until the reply arrives, and the worker must reply before it
can receive again. Use it for in-process command/query style interactions
where a return value is required, not for fire-and-forget data flow.

---

## 2. RabbitMQ — brokered messaging between services

RabbitMQ is the **broker** option: a standalone server that accepts messages
from producers and routes them to consumers via **exchanges** and **queues**.
Unlike ZeroMQ, it provides persistence, acknowledgements, and routing
semantics, and it decouples producers from consumers in time and space. In
snapshot-processor it is implemented in `tailucas_pylib.rabbit.py` using
`pika`.

### The topic-exchange pattern

The core abstraction is `MQConnection`, which wraps a `pika.BlockingConnection`
and a **topic exchange**. Producers publish to the exchange with a
**routing key** (a dot-separated topic string); consumers declare an
exclusive queue, bind it to the exchange with a **topic filter** (e.g. `#` for
everything, or `device.*` for a subset), and consume. The broker does the
routing — the producer never knows who is listening.

Connection and channel lifecycle is handled defensively: connections and
channels are recreated lazily when closed, and publishes retry on
`StreamLostError` before giving up. This resilience matters because a broker
connection is a long-lived, failure-prone resource, unlike an in-process
ZeroMQ socket.

### The bridge pattern (ZMQ ↔ RabbitMQ)

snapshot-processor treats RabbitMQ as an **external boundary** and bridges it
to the internal ZeroMQ pipeline. Two classes express this:

- `ZMQListener` — consumes from RabbitMQ and forwards into ZeroMQ (broker →
  in-process). It unpacks the MessagePack body and pushes a Python object
  onto a ZMQ socket. In `app/__main__.py` it is used as the control listener
  (`event.trigger.<device_topic>` → `URL_WORKER_APP`).
- `RabbitMQRelay` — receives from ZeroMQ and publishes to RabbitMQ
  (in-process → broker). It reads a `(topic, payload)` tuple from a PULL
  socket and publishes it to the exchange. In `app/__main__.py` it publishes
  image events and heartbeats on `event.notify.<device_topic>` /
  `event.heartbeat.<device_topic>`.

This is the general pattern for integrating a broker with an internal
pipeline: **keep the broker at the edge**, and translate at the boundary
rather than letting broker concepts leak into the core. Payloads are encoded
with MessagePack (`umsgpack`) for compact, cross-language-safe transport.

---

## 3. MQTT — lightweight pub/sub for constrained devices

MQTT is **not currently used** in snapshot-processor — there is no
`paho-mqtt`, `mosquitto`, or MQTT configuration anywhere in the project, and
the `mq` extra does not install an MQTT client. It is documented here as a
*possible* use, distinct from the two above.

MQTT is a **publish/subscribe** protocol designed for constrained
environments: small code footprint, low bandwidth, and tolerance for
unreliable networks. Clients publish to **topics** and subscribe to topic
filters; a broker relays messages between them. Its distinguishing features
are **QoS levels** (0 = at most once, 1 = at least once, 2 = exactly once),
**retained messages** (the last message on a topic is stored and delivered to
new subscribers), and **last-will** messages (published on behalf of a client
that disconnects unexpectedly).

### When MQTT is the right choice

MQTT overlaps with RabbitMQ's pub/sub capability, but the two are aimed at
different problems. Prefer MQTT when:

- the producers/consumers are **embedded or battery-powered devices** with
  limited CPU, memory, or network,
- the link is **lossy or intermittent** (QoS 1/2 and the client's
  keep-alive/reconnect behavior matter),
- you need **retained state** so late subscribers see the latest value.

Prefer RabbitMQ when you need richer routing (exchanges, bindings, headers),
durable queues, acknowledgements, or integration with an existing AMQP
ecosystem. Prefer ZeroMQ when the communication is **in-process** and you
want no broker at all.

### How it would fit this project

If MQTT were introduced, it would follow the same **bridge-at-the-edge**
pattern as RabbitMQ: a dedicated thread (or pair of threads) would translate
between MQTT topics and the internal ZeroMQ pipeline, keeping MQTT-specific
concerns (QoS, retained flags, topic structure) confined to that boundary.
The internal pipeline would remain unchanged, because the in-process
transport and the device-facing transport solve different problems and
should not be conflated.