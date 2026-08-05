"""HTTP transport for delivering Event Protocol envelopes over the vehicle LAN."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    encode_event_protocol_envelope,
    event_protocol_idempotency_key,
)


class EventProtocolHttpError(ConnectionError):
    """Raised when Runtime cannot durably acknowledge an HTTP envelope."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HttpResponse(Protocol):
    headers: Mapping[str, str]
    status: int

    def read(self, amount: int = -1) -> bytes:
        ...

    def __enter__(self) -> HttpResponse:
        ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        ...


HttpOpener = Callable[..., HttpResponse]


@dataclass(frozen=True)
class HttpEventProtocolSettings:
    endpoint: str
    timeout_seconds: float = 2.0
    bearer_token_file: Path | None = None
    max_response_bytes: int = 65_536

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Runtime endpoint must be an absolute http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Runtime endpoint must not contain embedded credentials")
        if parsed.fragment:
            raise ValueError("Runtime endpoint must not contain a URL fragment")
        if self.timeout_seconds <= 0:
            raise ValueError("HTTP Runtime timeout must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("HTTP Runtime response limit must be positive")


class HttpEventProtocolTransport:
    """POST canonical Event Protocol JSON and require a downstream receipt."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 2.0,
        bearer_token_file: str | Path | None = None,
        max_response_bytes: int = 65_536,
        opener: HttpOpener = urlopen,
    ) -> None:
        token_path = None if bearer_token_file is None else Path(bearer_token_file)
        self.settings = HttpEventProtocolSettings(
            endpoint=endpoint,
            timeout_seconds=float(timeout_seconds),
            bearer_token_file=token_path,
            max_response_bytes=max_response_bytes,
        )
        self.opener = opener

    def publish_envelope(self, envelope: EventProtocolEnvelope) -> str:
        encoded = encode_event_protocol_envelope(envelope)
        idempotency_key = event_protocol_idempotency_key(envelope)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Idempotency-Key": idempotency_key,
            "User-Agent": "velvet-audio-studio/0.1",
            "X-Velvet-Event-ID": idempotency_key,
        }
        token = self._bearer_token()
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        request = Request(
            self.settings.endpoint,
            data=encoded,
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.settings.timeout_seconds) as response:
                status = _response_status(response)
                body = _read_limited(response, self.settings.max_response_bytes)
                if not 200 <= status < 300:
                    raise EventProtocolHttpError(
                        _failure_message(status, body),
                        status_code=status,
                    )
                receipt = _receipt_identifier(response.headers, body)
        except HTTPError as exc:
            body = _read_limited(exc, self.settings.max_response_bytes)
            receipt = _receipt_identifier(exc.headers or {}, body)
            if exc.code == 409 and receipt:
                return receipt
            raise EventProtocolHttpError(
                _failure_message(exc.code, body),
                status_code=exc.code,
            ) from exc
        except EventProtocolHttpError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise EventProtocolHttpError(
                f"Runtime HTTP delivery failed: {type(reason).__name__}: {reason}"
            ) from exc

        if not receipt:
            raise EventProtocolHttpError(
                "Runtime accepted the Event Protocol envelope without a receipt identifier",
                status_code=status,
            )
        return receipt

    def _bearer_token(self) -> str | None:
        path = self.settings.bearer_token_file
        if path is None:
            return None
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise EventProtocolHttpError(
                f"Runtime bearer token could not be read from {path}: {exc}"
            ) from exc
        if not token:
            raise EventProtocolHttpError(
                f"Runtime bearer token file is empty: {path}"
            )
        return token


def _response_status(response: object) -> int:
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        code = getcode()
        if isinstance(code, int):
            return code
    raise EventProtocolHttpError("Runtime HTTP response did not expose a status code")


def _read_limited(stream: BinaryIO, maximum: int) -> bytes:
    payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise EventProtocolHttpError(
            f"Runtime HTTP response exceeded {maximum} bytes"
        )
    return payload


def _receipt_identifier(headers: Mapping[str, str], body: bytes) -> str | None:
    for name in ("X-Velvet-Receipt-ID", "X-Receipt-ID"):
        value = headers.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if not body.strip():
        return None
    try:
        decoded: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, Mapping):
        return None
    for key in ("receipt_id", "receiptId", "id"):
        value = decoded.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _failure_message(status: int, body: bytes) -> str:
    detail = ""
    if body:
        try:
            decoded = body.decode("utf-8", errors="replace").strip()
        except Exception:
            decoded = ""
        if decoded:
            detail = f": {decoded[:512]}"
    return f"Runtime HTTP delivery returned status {status}{detail}"
