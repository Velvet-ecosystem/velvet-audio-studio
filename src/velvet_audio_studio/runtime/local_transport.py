"""Local Event Protocol transports for development and fail-closed operation."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import sys
from typing import TextIO

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.event_protocol import EventProtocolEnvelope


class JsonlEventProtocolTransport:
    """Write Event Protocol envelopes as one canonical JSON object per line."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stdout if stream is None else stream

    def publish_envelope(self, envelope: EventProtocolEnvelope) -> str:
        encoded = json.dumps(
            asdict(envelope),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        self.stream.write(encoded + "\n")
        self.stream.flush()
        digest = sha256(encoded.encode("utf-8")).hexdigest()
        return f"event-protocol-jsonl-{digest[:24]}"


class UnavailableRuntimePublisher:
    """Explicitly fail every publish so the durable queue retains all events."""

    def __init__(self, reason: str = "Velvet Runtime transport is not configured") -> None:
        if not reason.strip():
            raise ValueError("unavailable Runtime reason cannot be empty")
        self.reason = reason.strip()

    def publish(self, event: RuntimeAudioEvent) -> str:
        raise ConnectionError(f"{self.reason}: {event.event}")
