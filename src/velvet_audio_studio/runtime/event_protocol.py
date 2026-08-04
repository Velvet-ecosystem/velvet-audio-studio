from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent


@dataclass(frozen=True)
class EventProtocolEnvelope:
    event_type: str
    source_id: str
    sequence: int
    occurred_at_monotonic_ns: int
    payload: dict[str, object]


class EventProtocolTransport(Protocol):
    def publish_envelope(self, envelope: EventProtocolEnvelope) -> str:
        """Publish one Event Protocol envelope and return its receipt identifier."""
        ...


class EventProtocolPublisher:
    """Adapts studio RuntimeAudioEvent values to Velvet Event Protocol envelopes."""

    def __init__(self, transport: EventProtocolTransport) -> None:
        self.transport = transport

    def publish(self, event: RuntimeAudioEvent) -> str:
        envelope = EventProtocolEnvelope(
            event_type=event.event,
            source_id=event.source_id,
            sequence=event.packet_sequence,
            occurred_at_monotonic_ns=event.occurred_at_monotonic_ns,
            payload=dict(event.payload),
        )
        return self.transport.publish_envelope(envelope)
