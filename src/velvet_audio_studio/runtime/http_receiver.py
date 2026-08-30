"""Reference Runtime HTTP receiver for end-to-end Event Protocol validation."""

from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Callable, Mapping

from velvet_audio_studio.runtime.acknowledgement_store import (
    AcknowledgementConflictError,
    AcknowledgementStoreError,
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    event_protocol_idempotency_key,
)


EnvelopeValidator = Callable[[EventProtocolEnvelope], None]


@dataclass(frozen=True)
class ReceiverResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


class EventProtocolReceiver:
    """Validate, acknowledge, and deduplicate one Runtime Event Protocol endpoint."""

    def __init__(
        self,
        store: SqliteAcknowledgementStore,
        *,
        endpoint_path: str = "/v1/events",
        health_path: str = "/health",
        max_request_bytes: int = 1_048_576,
        bearer_token_file: str | Path | None = None,
        envelope_validator: EnvelopeValidator | None = None,
    ) -> None:
        if not endpoint_path.startswith("/") or "?" in endpoint_path or "#" in endpoint_path:
            raise ValueError("receiver endpoint path must be an absolute URL path")
        if not health_path.startswith("/") or "?" in health_path or "#" in health_path:
            raise ValueError("receiver health path must be an absolute URL path")
        if endpoint_path == health_path:
            raise ValueError("receiver endpoint and health paths must differ")
        if max_request_bytes <= 0:
            raise ValueError("receiver request limit must be positive")
        self.store = store
        self.endpoint_path = endpoint_path
        self.health_path = health_path
        self.max_request_bytes = max_request_bytes
        self.bearer_token_file = (
            None
            if bearer_token_file is None
            else Path(bearer_token_file).expanduser().resolve()
        )
        self.envelope_validator = envelope_validator

    def accept(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> ReceiverResponse:
        if path != self.endpoint_path:
            return _json_response(404, {"error": "not_found"})
        if method.upper() != "POST":
            return _json_response(
                405,
                {"error": "method_not_allowed"},
                extra_headers=(("Allow", "POST"),),
            )
        if len(body) > self.max_request_bytes:
            return _json_response(
                413,
                {
                    "error": "request_too_large",
                    "max_request_bytes": self.max_request_bytes,
                },
            )
        content_type = (_header(headers, "Content-Type") or "").split(";", 1)[0].strip()
        if content_type.casefold() != "application/json":
            return _json_response(
                415,
                {"error": "content_type_must_be_application_json"},
            )

        authentication_error = self._authentication_error(headers)
        if authentication_error is not None:
            return _json_response(
                401,
                {"error": authentication_error},
                extra_headers=(("WWW-Authenticate", "Bearer"),),
            )

        try:
            envelope = parse_event_protocol_envelope(body)
        except ValueError as exc:
            return _json_response(
                400,
                {"error": "invalid_event_protocol_envelope", "detail": str(exc)},
            )

        if self.envelope_validator is not None:
            try:
                self.envelope_validator(envelope)
            except ValueError as exc:
                return _json_response(
                    422,
                    {"error": "event_payload_rejected", "detail": str(exc)},
                )

        idempotency_key = (_header(headers, "Idempotency-Key") or "").strip()
        velvet_event_id = (_header(headers, "X-Velvet-Event-ID") or "").strip()
        if not idempotency_key:
            return _json_response(400, {"error": "idempotency_key_required"})
        if velvet_event_id and velvet_event_id != idempotency_key:
            return _json_response(
                400,
                {"error": "event_id_headers_disagree"},
            )
        expected_key = event_protocol_idempotency_key(envelope)
        if not compare_digest(idempotency_key, expected_key):
            return _json_response(
                400,
                {"error": "idempotency_key_does_not_match_envelope"},
            )

        try:
            acknowledgement = self.store.acknowledge(idempotency_key, envelope)
        except AcknowledgementConflictError as exc:
            return _json_response(
                409,
                {"error": "idempotency_conflict", "detail": str(exc)},
            )
        except AcknowledgementStoreError as exc:
            return _json_response(
                503,
                {"error": "acknowledgement_store_unavailable", "detail": str(exc)},
                extra_headers=(("Retry-After", "1"),),
            )

        status = 409 if acknowledgement.duplicate else 202
        return _json_response(
            status,
            {
                "receipt_id": acknowledgement.receipt_id,
                "duplicate": acknowledgement.duplicate,
                "duplicate_count": acknowledgement.duplicate_count,
                "idempotency_key": acknowledgement.idempotency_key,
            },
            extra_headers=(
                ("X-Velvet-Receipt-ID", acknowledgement.receipt_id),
                ("X-Velvet-Idempotency-Key", acknowledgement.idempotency_key),
            ),
        )

    def health(self) -> ReceiverResponse:
        try:
            accepted_events = self.store.count()
        except AcknowledgementStoreError as exc:
            return _json_response(
                503,
                {"status": "degraded", "detail": str(exc)},
            )
        return _json_response(
            200,
            {
                "status": "ready",
                "accepted_events": accepted_events,
                "endpoint_path": self.endpoint_path,
            },
        )

    def _authentication_error(self, headers: Mapping[str, str]) -> str | None:
        path = self.bearer_token_file
        if path is None:
            return None
        try:
            expected = path.read_text(encoding="utf-8").strip()
        except OSError:
            return "bearer_token_unavailable"
        if not expected:
            return "bearer_token_unavailable"
        authorization = (_header(headers, "Authorization") or "").strip()
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            return "bearer_token_required"
        supplied = authorization[len(prefix) :].strip()
        if not supplied or not compare_digest(supplied, expected):
            return "bearer_token_invalid"
        return None


def parse_event_protocol_envelope(body: bytes) -> EventProtocolEnvelope:
    if not body:
        raise ValueError("request body cannot be empty")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("request body must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("envelope must be a JSON object")

    expected_fields = {
        "event_type",
        "source_id",
        "sequence",
        "occurred_at_monotonic_ns",
        "payload",
    }
    actual_fields = set(decoded)
    missing = sorted(expected_fields - actual_fields)
    extra = sorted(actual_fields - expected_fields)
    if missing:
        raise ValueError(f"envelope is missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"envelope contains unknown fields: {', '.join(extra)}")

    event_type = _nonempty_text(decoded["event_type"], "event_type")
    source_id = _nonempty_text(decoded["source_id"], "source_id")
    sequence = _nonnegative_integer(decoded["sequence"], "sequence")
    occurred_ns = _nonnegative_integer(
        decoded["occurred_at_monotonic_ns"],
        "occurred_at_monotonic_ns",
    )
    payload = decoded["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    return EventProtocolEnvelope(
        event_type=event_type,
        source_id=source_id,
        sequence=sequence,
        occurred_at_monotonic_ns=occurred_ns,
        payload=payload,
    )


def build_runtime_receiver_server(
    host: str,
    port: int,
    receiver: EventProtocolReceiver,
) -> ThreadingHTTPServer:
    if not host.strip():
        raise ValueError("receiver host cannot be empty")
    if port < 0 or port > 65_535:
        raise ValueError("receiver port must be in the range 0 to 65535")

    class ReceiverHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "VelvetRuntimeReceiver/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == receiver.health_path:
                self._write(receiver.health())
            else:
                self._write(_json_response(404, {"error": "not_found"}))

        def do_POST(self) -> None:  # noqa: N802
            length_text = self.headers.get("Content-Length")
            if length_text is None:
                self._write(_json_response(411, {"error": "content_length_required"}))
                return
            try:
                length = int(length_text)
            except ValueError:
                self._write(_json_response(400, {"error": "invalid_content_length"}))
                return
            if length < 0:
                self._write(_json_response(400, {"error": "invalid_content_length"}))
                return
            if length > receiver.max_request_bytes:
                self.close_connection = True
                self._write(
                    _json_response(
                        413,
                        {
                            "error": "request_too_large",
                            "max_request_bytes": receiver.max_request_bytes,
                        },
                    )
                )
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self._write(_json_response(400, {"error": "incomplete_request_body"}))
                return
            response = receiver.accept(
                method="POST",
                path=self.path,
                headers=self.headers,
                body=body,
            )
            self._write(response)

        def _write(self, response: ReceiverResponse) -> None:
            self.send_response(response.status)
            for name, value in response.headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)
                self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer((host, port), ReceiverHandler)
    server.daemon_threads = True
    return server


def _json_response(
    status: int,
    payload: dict[str, object],
    *,
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> ReceiverResponse:
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    headers = (
        ("Content-Type", "application/json; charset=utf-8"),
        ("Cache-Control", "no-store"),
    ) + extra_headers
    return ReceiverResponse(status=status, headers=headers, body=body)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    direct = headers.get(name)
    if direct is not None:
        return direct
    folded = name.casefold()
    for candidate, value in headers.items():
        if candidate.casefold() == folded:
            return value
    return None


def _nonempty_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value
