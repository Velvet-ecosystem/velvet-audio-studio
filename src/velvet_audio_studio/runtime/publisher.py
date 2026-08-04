from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent


class RuntimeEventPublisher(Protocol):
    """Hardware-neutral boundary for delivering audio events to Velvet Runtime."""

    def publish(self, event: RuntimeAudioEvent) -> str:
        """Publish one event and return the downstream receipt identifier."""
        ...


@dataclass(frozen=True)
class DeliveryReceipt:
    event: str
    packet_sequence: int
    delivered: bool
    downstream_receipt_id: str | None
    degraded_reason: str | None = None


@dataclass(frozen=True)
class DeliveryBatch:
    receipts: tuple[DeliveryReceipt, ...]

    @property
    def delivered_count(self) -> int:
        return sum(receipt.delivered for receipt in self.receipts)

    @property
    def failed_count(self) -> int:
        return len(self.receipts) - self.delivered_count

    @property
    def degraded(self) -> bool:
        return self.failed_count > 0


class InMemoryRuntimePublisher:
    """Deterministic test publisher used before the real Event Protocol adapter lands."""

    def __init__(self) -> None:
        self.events: list[RuntimeAudioEvent] = []

    def publish(self, event: RuntimeAudioEvent) -> str:
        self.events.append(event)
        return f"runtime-audio-{len(self.events):06d}"


class AudioRuntimeBridge:
    """Delivers ordered audio events without binding the studio to one bus."""

    def __init__(self, publisher: RuntimeEventPublisher) -> None:
        self.publisher = publisher

    def deliver(self, events: Sequence[RuntimeAudioEvent]) -> DeliveryBatch:
        receipts: list[DeliveryReceipt] = []
        for event in events:
            try:
                downstream_receipt_id = self.publisher.publish(event)
            except Exception as exc:  # Boundary must convert transport failure into data.
                receipts.append(
                    DeliveryReceipt(
                        event=event.event,
                        packet_sequence=event.packet_sequence,
                        delivered=False,
                        downstream_receipt_id=None,
                        degraded_reason=f"runtime publish failed: {type(exc).__name__}: {exc}",
                    )
                )
                continue

            if not downstream_receipt_id:
                receipts.append(
                    DeliveryReceipt(
                        event=event.event,
                        packet_sequence=event.packet_sequence,
                        delivered=False,
                        downstream_receipt_id=None,
                        degraded_reason="runtime publisher returned an empty receipt identifier",
                    )
                )
                continue

            receipts.append(
                DeliveryReceipt(
                    event=event.event,
                    packet_sequence=event.packet_sequence,
                    delivered=True,
                    downstream_receipt_id=downstream_receipt_id,
                )
            )

        return DeliveryBatch(tuple(receipts))
