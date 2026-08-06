# Runtime receiver deployment

The repository includes a reference Runtime-side Event Protocol receiver. It is intended for vehicle-LAN integration tests and for proving durable acknowledgement and dispatch semantics before the final Velvet Runtime adapters are connected.

The receiver performs five jobs:

1. validates the HTTP path, content type, envelope schema, and body size
2. optionally authenticates a bearer token loaded from disk on every request
3. recalculates the canonical Event Protocol idempotency key
4. durably stores the canonical envelope and receipt in SQLite
5. returns `202 Accepted` for a new event or `409 Conflict` with the same receipt for a replay

A receiver receipt means the event has durably landed in the ingress database. It does not claim that Court, routing, or an organ has processed the event.

The same SQLite transaction also creates a pending dispatch row with a stable `runtime-dispatch-*` identity. Runtime workers can later claim that row through `SqliteIngressDispatchQueue` without a gap between acceptance and dispatch eligibility.

## Local launch

```bash
velvet-audio serve-runtime \
  --host 127.0.0.1 \
  --port 8765 \
  --database ./runtime-acknowledgements.sqlite3 \
  --path /v1/events \
  --health-path /health
```

With bearer authentication:

```bash
velvet-audio serve-runtime \
  --host 0.0.0.0 \
  --port 8765 \
  --database /var/lib/velvet-runtime-receiver/acknowledgements.sqlite3 \
  --bearer-token-file /etc/velvet-runtime-receiver/runtime.token
```

The token file should contain only the token text and should be readable by the receiver service account. The receiver reloads it for every request so an atomic file replacement rotates credentials without restarting the daemon.

## Health endpoint

```http
GET /health HTTP/1.1
```

A ready receiver returns:

```json
{"accepted_events":12,"endpoint_path":"/v1/events","status":"ready"}
```

The current health response reports durable ingress acceptance. Dispatch state is available through `SqliteIngressDispatchQueue.stats()` and the long-running worker emits its own queue, lease, retry, quarantine, and infrastructure health events.

## Durable acknowledgement database

The SQLite ledger uses WAL mode and synchronous durable writes. Each acknowledgement row records:

- the canonical idempotency key
- the SHA-256 digest of the canonical envelope
- the stable Runtime ingress receipt identifier
- event type, source, sequence, and monotonic occurrence time
- the canonical envelope bytes
- first accepted and last replay timestamps
- duplicate replay count

The same database stores dispatch state:

- stable dispatch ID
- pending, claimed, or processed status
- worker and claim token
- lease timestamps
- attempt count and last error
- processed time
- final Court denial, route, organ, or quarantine receipt

The quarantine-capable worker also adds:

- repeated failure fingerprint and evidence count
- poison-classification reason
- stable `runtime-quarantine-*` receipt
- quarantine timestamp

The same idempotency key presented with different canonical envelope bytes is rejected as a conflict. Duplicate delivery of the same event increments the replay count and returns the original receipt.

Opening an acknowledgement database created before dispatch support automatically backfills pending dispatch state for every existing event. Original ingress receipts remain unchanged.

## Dispatch ordering and Court

The oldest unprocessed event is the dispatch gate. A live claim blocks every later event, preserving the order in which Runtime durably accepted evidence.

Expired claims may be reclaimed. The stable dispatch ID does not change, allowing Court and routers to deduplicate retries after timeout or process death.

`CourtRoutedIngressHandler` places a durable Court decision before routing. Approved events carry a bounded capability to the router. Durable denials complete with their Court denial receipt and never reach routing.

`RuntimeDispatchWorker` adds continuous polling, bounded retry backoff, lease renewal during slow Court or route operations, graceful stop behavior, and conservative quarantine. Generic repeated failures remain retryable. Only explicitly classified permanent failures may be quarantined, and only after the same fingerprint reaches the configured threshold.

The full claim, lease, receipt, migration, and crash-gap doctrine is in `docs/runtime_ingress_dispatch.md`. Long-running worker behavior is in `docs/runtime_dispatch_worker.md`.

## Worker assembly boundary

Runtime can assemble the worker around its real Court and router:

```python
assembly = build_runtime_dispatch_worker(
    "/var/lib/velvet-runtime-receiver/acknowledgements.sqlite3",
    court,
    router,
    worker_id="runtime-dispatch-01",
)

assembly.worker.run(stop_requested=shutdown_latch.is_requested)
```

The repository deliberately does not provide an allow-all Court, dummy capability, or fake route receipt.

For that reason, the reference receiver has a systemd unit today, but the dispatch worker does not yet ship with a production unit. A worker unit belongs in the final Runtime integration once the actual Court ledger, capability vocabulary, router, health sink, and shutdown limits are bound.

## systemd installation

The reference receiver unit is `packaging/systemd/velvet-runtime-receiver.service`.

Example installation:

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin velvet-runtime-receiver
sudo install -d -m 0750 -o velvet-runtime-receiver -g velvet-runtime-receiver /etc/velvet-runtime-receiver
sudo install -m 0640 -o root -g velvet-runtime-receiver runtime.token /etc/velvet-runtime-receiver/runtime.token
sudo install -m 0644 packaging/systemd/velvet-runtime-receiver.service /etc/systemd/system/velvet-runtime-receiver.service
sudo systemctl daemon-reload
sudo systemctl enable --now velvet-runtime-receiver.service
```

The unit binds to port `8765`, writes acknowledgements and dispatch state only inside `/var/lib/velvet-runtime-receiver`, uses a read-only application tree, has no device access, and shuts down through SIGTERM.

## Audio-node configuration

Point the audio service at the receiver:

```yaml
network:
  transport: ethernet
  event_protocol_transport: http_json
  runtime_endpoint: http://velvet-runtime.local:8765/v1/events
  request_timeout_seconds: 2.0
  bearer_token_file: /etc/velvet-audio/runtime.token
  max_response_bytes: 65536
```

The audio node keeps failed deliveries in its ordered retry journal. Once the receiver is reachable, replay resumes from the oldest undelivered event.
