"""Local Event Protocol transports for development and fail-closed operation."""

from __future__ import annotations

import sys
from typing import TextIO

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    encode_event_protocol_envelope,
    event_protocol_idempotency_key,
)


class JsonlEventProtocolTransport:
    """Write Event Protocol envelopes as one canonical JSON object per line."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stdout if stream is None else stream

    def publish_envelope(self, envelope: EventProtocolEnvelope) -> str:
        encoded = encode_event_protocol_envelope(envelope)
        self.stream.write(encoded.decode("utf-8") + "\n")
        self.stream.flush()
        digest = event_protocol_idempotency_key(envelope)
        return f"event-protocol-jsonl-{digest[:24]}"


class UnavailableRuntimePublisher:
    """Explicitly fail every publish so the durable queue retains all events."""

    def __init__(self, reason: str = "Velvet Runtime transport is not configured") -> None:
        if not reason.strip():
            raise ValueError("unavailable Runtime reason cannot be empty")
        self.reason = reason.strip()

    def publish(self, event: RuntimeAudioEvent) -> str:
        raise ConnectionError(f"{self.reason}: {event.event}")
