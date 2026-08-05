from __future__ import annotations

from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    encode_event_protocol_envelope,
    event_protocol_idempotency_key,
)
from velvet_audio_studio.runtime.http_transport import (
    EventProtocolHttpError,
    HttpEventProtocolTransport,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 202,
        body: bytes = b'{"receipt_id":"runtime-receipt-1"}',
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._stream = BytesIO(body)

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def _envelope() -> EventProtocolEnvelope:
    return EventProtocolEnvelope(
        event_type="audio.capture.active",
        source_id="octo.capture.primary",
        sequence=7,
        occurred_at_monotonic_ns=1_234_000_000,
        payload={"state": "active", "frames": 480},
    )


def test_http_transport_posts_canonical_envelope_and_requires_receipt(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "runtime.token"
    token_path.write_text("first-token\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    envelope = _envelope()
    transport = HttpEventProtocolTransport(
        "http://runtime.local:8765/v1/events",
        timeout_seconds=1.5,
        bearer_token_file=token_path,
        opener=opener,
    )

    receipt = transport.publish_envelope(envelope)

    request = captured["request"]
    assert isinstance(request, Request)
    headers = {name.casefold(): value for name, value in request.header_items()}
    assert receipt == "runtime-receipt-1"
    assert captured["timeout"] == 1.5
    assert request.full_url == "http://runtime.local:8765/v1/events"
    assert request.get_method() == "POST"
    assert request.data == encode_event_protocol_envelope(envelope)
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["accept"] == "application/json"
    assert headers["authorization"] == "Bearer first-token"
    assert headers["idempotency-key"] == event_protocol_idempotency_key(envelope)
    assert headers["x-velvet-event-id"] == event_protocol_idempotency_key(envelope)


def test_bearer_token_is_reloaded_for_each_publish(tmp_path: Path) -> None:
    token_path = tmp_path / "runtime.token"
    token_path.write_text("token-one", encoding="utf-8")
    authorizations: list[str] = []

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        del timeout
        headers = {name.casefold(): value for name, value in request.header_items()}
        authorizations.append(headers["authorization"])
        return FakeResponse()

    transport = HttpEventProtocolTransport(
        "https://runtime.local/v1/events",
        bearer_token_file=token_path,
        opener=opener,
    )
    transport.publish_envelope(_envelope())
    token_path.write_text("token-two", encoding="utf-8")
    transport.publish_envelope(_envelope())

    assert authorizations == ["Bearer token-one", "Bearer token-two"]


def test_receipt_header_allows_empty_success_body() -> None:
    transport = HttpEventProtocolTransport(
        "http://runtime.local/v1/events",
        opener=lambda request, timeout: FakeResponse(
            status=204,
            body=b"",
            headers={"X-Velvet-Receipt-ID": "receipt-from-header"},
        ),
    )

    assert transport.publish_envelope(_envelope()) == "receipt-from-header"


def test_duplicate_conflict_is_accepted_only_with_existing_receipt() -> None:
    headers = Message()
    headers["Content-Type"] = "application/json"

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        del request, timeout
        raise HTTPError(
            "http://runtime.local/v1/events",
            409,
            "Conflict",
            headers,
            BytesIO(b'{"receipt_id":"existing-receipt"}'),
        )

    transport = HttpEventProtocolTransport(
        "http://runtime.local/v1/events",
        opener=opener,
    )

    assert transport.publish_envelope(_envelope()) == "existing-receipt"


def test_success_without_receipt_remains_undelivered() -> None:
    transport = HttpEventProtocolTransport(
        "http://runtime.local/v1/events",
        opener=lambda request, timeout: FakeResponse(body=b"{}"),
    )

    with pytest.raises(EventProtocolHttpError, match="without a receipt") as caught:
        transport.publish_envelope(_envelope())

    assert caught.value.status_code == 202


def test_network_failure_is_converted_to_transport_error() -> None:
    def opener(request: Request, *, timeout: float) -> FakeResponse:
        del request, timeout
        raise URLError("route unavailable")

    transport = HttpEventProtocolTransport(
        "http://runtime.local/v1/events",
        opener=opener,
    )

    with pytest.raises(EventProtocolHttpError, match="route unavailable"):
        transport.publish_envelope(_envelope())


def test_response_size_is_bounded() -> None:
    transport = HttpEventProtocolTransport(
        "http://runtime.local/v1/events",
        max_response_bytes=8,
        opener=lambda request, timeout: FakeResponse(body=b"123456789"),
    )

    with pytest.raises(EventProtocolHttpError, match="exceeded 8 bytes"):
        transport.publish_envelope(_envelope())


def test_endpoint_rejects_embedded_credentials() -> None:
    with pytest.raises(ValueError, match="embedded credentials"):
        HttpEventProtocolTransport("http://user:secret@runtime.local/v1/events")


def test_empty_token_file_fails_without_leaking_event(tmp_path: Path) -> None:
    token_path = tmp_path / "empty-runtime.token"
    token_path.write_text("\n", encoding="utf-8")
    transport = HttpEventProtocolTransport(
        "http://runtime.local/v1/events",
        bearer_token_file=token_path,
        opener=lambda request, timeout: FakeResponse(),
    )

    with pytest.raises(EventProtocolHttpError, match="token file is empty"):
        transport.publish_envelope(_envelope())
