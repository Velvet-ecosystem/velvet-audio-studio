# Runtime receiver deployment

The repository includes a reference Runtime-side Event Protocol receiver. It is intended for vehicle-LAN integration tests and for proving durable acknowledgement semantics before the final Velvet Runtime ingress adapter is connected.

The receiver performs five jobs:

1. validates the HTTP path, content type, envelope schema, and body size
2. optionally authenticates a bearer token loaded from disk on every request
3. recalculates the canonical Event Protocol idempotency key
4. durably stores the canonical envelope and receipt in SQLite
5. returns `202 Accepted` for a new event or `409 Conflict` with the same receipt for a replay

A receiver receipt means the event has durably landed in the ingress database. It does not claim that every downstream Runtime consumer has processed the event. Final Court, routing, capability, and organ-dispatch integration remains a separate Runtime responsibility.

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

## Durable acknowledgement database

The SQLite ledger uses WAL mode and synchronous durable writes. Each row records:

- the canonical idempotency key
- the SHA-256 digest of the canonical envelope
- the stable Runtime receipt identifier
- event type, source, sequence, and monotonic occurrence time
- the canonical envelope bytes
- first accepted and last replay timestamps
- duplicate replay count

The same idempotency key presented with different canonical envelope bytes is rejected as a conflict. Duplicate delivery of the same event increments the replay count and returns the original receipt.

## systemd installation

The reference unit is `packaging/systemd/velvet-runtime-receiver.service`.

Example installation:

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin velvet-runtime-receiver
sudo install -d -m 0750 -o velvet-runtime-receiver -g velvet-runtime-receiver /etc/velvet-runtime-receiver
sudo install -m 0640 -o root -g velvet-runtime-receiver runtime.token /etc/velvet-runtime-receiver/runtime.token
sudo install -m 0644 packaging/systemd/velvet-runtime-receiver.service /etc/systemd/system/velvet-runtime-receiver.service
sudo systemctl daemon-reload
sudo systemctl enable --now velvet-runtime-receiver.service
```

The unit binds to port `8765`, writes acknowledgements only inside `/var/lib/velvet-runtime-receiver`, uses a read-only application tree, has no device access, and shuts down through SIGTERM.

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
