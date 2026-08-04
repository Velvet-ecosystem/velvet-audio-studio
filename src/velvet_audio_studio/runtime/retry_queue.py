from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.publisher import DeliveryBatch, DeliveryReceipt, RuntimeEventPublisher


@dataclass(frozen=True)
class RetryQueueStatus:
    pending_count: int
    oldest_packet_sequence: int | None
    newest_packet_sequence: int | None


class OrderedRetryQueue:
    """Buffers failed Runtime events and replays them in original order."""

    def __init__(self, *, max_pending: int = 1024) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._pending: deque[RuntimeAudioEvent] = deque()
        self.max_pending = max_pending

    @property
    def status(self) -> RetryQueueStatus:
        sequences = [event.packet_sequence for event in self._pending]
        return RetryQueueStatus(
            pending_count=len(self._pending),
            oldest_packet_sequence=min(sequences) if sequences else None,
            newest_packet_sequence=max(sequences) if sequences else None,
        )

    def enqueue(self, events: Iterable[RuntimeAudioEvent]) -> None:
        for event in events:
            if len(self._pending) >= self.max_pending:
                raise OverflowError("Runtime retry queue is full")
            self._pending.append(event)

    def deliver(self, publisher: RuntimeEventPublisher) -> DeliveryBatch:
        receipts: list[DeliveryReceipt] = []
        while self._pending:
            event = self._pending[0]
            try:
                receipt_id = publisher.publish(event)
            except Exception as exc:
                receipts.append(
                    DeliveryReceipt(
                        event=event.event,
                        packet_sequence=event.packet_sequence,
                        delivered=False,
                        downstream_receipt_id=None,
                        degraded_reason=f"runtime retry failed: {type(exc).__name__}: {exc}",
                    )
                )
                break

            if not receipt_id:
                receipts.append(
                    DeliveryReceipt(
                        event=event.event,
                        packet_sequence=event.packet_sequence,
                        delivered=False,
                        downstream_receipt_id=None,
                        degraded_reason="runtime retry returned an empty receipt identifier",
                    )
                )
                break

            self._pending.popleft()
            receipts.append(
                DeliveryReceipt(
                    event=event.event,
                    packet_sequence=event.packet_sequence,
                    delivered=True,
                    downstream_receipt_id=receipt_id,
                )
            )

        return DeliveryBatch(tuple(receipts))
