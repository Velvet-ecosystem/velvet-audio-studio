# Runtime HTTP Event Protocol contract

The audio studio sends one Event Protocol envelope per HTTP request. Capture and service code do not depend on HTTP directly; this transport sits behind `EventProtocolTransport` and the durable ordered retry queue.

## Request

```http
POST /v1/events HTTP/1.1
Content-Type: application/json; charset=utf-8
Accept: application/json
Idempotency-Key: <sha256 of canonical envelope JSON>
X-Velvet-Event-ID: <same sha256 value>
Authorization: Bearer <optional token>
```

The body is canonical UTF-8 JSON with sorted keys and no insignificant whitespace:

```json
{
  "event_type": "audio.capture.active",
  "occurred_at_monotonic_ns": 1234000000,
  "payload": {
    "state": "active"
  },
  "sequence": 7,
  "source_id": "octo.capture.primary"
}
```

Runtime should use the idempotency key to recognize replayed journal entries. The same envelope always produces the same key across JSONL and HTTP transports.

## Successful acknowledgement

Runtime must return a non-empty receipt identifier. Any 2xx status is accepted when one of these forms is present.

JSON response:

```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{"receipt_id":"runtime-receipt-000123"}
```

The JSON keys `receipt_id`, `receiptId`, and `id` are recognized.

Header response:

```http
HTTP/1.1 204 No Content
X-Velvet-Receipt-ID: runtime-receipt-000123
```

`X-Receipt-ID` is also recognized.

A 2xx response without a receipt is not considered delivered. The event remains in the durable queue.

## Duplicate replay

Runtime may return `409 Conflict` when the idempotency key has already been accepted. The conflict counts as acknowledged only when the response supplies the existing receipt through one of the supported JSON fields or headers.

```http
HTTP/1.1 409 Conflict
Content-Type: application/json

{"receipt_id":"runtime-receipt-000123"}
```

A bare `409` remains a delivery failure because the audio node cannot prove which durable receipt owns the event.

## Failure behavior

The following conditions leave the event queued in original order:

- connection refusal, DNS failure, route loss, or timeout
- non-2xx status without acknowledged duplicate semantics
- response larger than `network.max_response_bytes`
- malformed or missing receipt identifier
- missing or empty bearer-token file when authentication is configured

Delivery stops at the first failed event. Later events do not jump ahead. Backlog health, compaction, restart restoration, and replay remain governed by the existing durable queue policy.

## Authentication

`network.bearer_token_file` points to a file containing only the bearer token text. The file is read for every publish so operators may rotate the token atomically without editing YAML or restarting the audio service. Endpoint URLs containing embedded credentials are rejected.
