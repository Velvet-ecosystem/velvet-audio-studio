from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.backlog_policy import (
    BacklogHealth,
    CompactionResult,
    compact_backlog,
)
from velvet_audio_studio.runtime.publisher import DeliveryBatch, RuntimeEventPublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal
from velvet_audio_studio.runtime.retry_queue import OrderedRetryQueue, RetryQueueStatus


@dataclass(frozen=True)
class DurableRetryStatus:
    queue: RetryQueueStatus
    journal_path: str


def event_idempotency_key(event: RuntimeAudioEvent) -> str:
    canonical = json.dumps(
        {
            "event": event.event,
            "source_id": event.source_id,
            "occurred_at_monotonic_ns": event.occurred_at_monotonic_ns,
            "packet_sequence": event.packet_sequence,
            "payload": event.payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class DurableOrderedRetryQueue:
    """Ordered retry queue mirrored to disk after every state change."""

    def __init__(self, journal: JsonlRetryJournal, *, max_pending: int = 1024) -> None:
        self.journal = journal
        self.queue = OrderedRetryQueue(max_pending=max_pending)
        restored = journal.load()
        self.queue.enqueue(restored)

    @property
    def status(self) -> DurableRetryStatus:
        return DurableRetryStatus(self.queue.status, str(self.journal.path))

    def health(
        self,
        *,
        observed_at_monotonic_ns: int,
        capacity_warning_ratio: float = 0.75,
        max_age_ms: int = 30_000,
    ) -> BacklogHealth:
        return self.queue.health(
            observed_at_monotonic_ns=observed_at_monotonic_ns,
            capacity_warning_ratio=capacity_warning_ratio,
            max_age_ms=max_age_ms,
        )

    def enqueue(self, events: Iterable[RuntimeAudioEvent]) -> None:
        existing = {
            event_idempotency_key(event)
            for event in self.queue.snapshot()
        }
        additions: list[RuntimeAudioEvent] = []
        for event in events:
            key = event_idempotency_key(event)
            if key in existing:
                continue
            existing.add(key)
            additions.append(event)

        self.queue.enqueue(additions)
        self._persist_pending()

    def deliver(self, publisher: RuntimeEventPublisher) -> DeliveryBatch:
        batch = self.queue.deliver(publisher)
        self._persist_pending()
        return batch

    def compact_and_persist(self) -> CompactionResult:
        """Compact eligible telemetry and atomically replace the durable journal.

        The journal is replaced before the in-memory queue. If persistence fails,
        the live queue remains untouched and may be retried safely.
        """
        original = self.queue.snapshot()
        result = compact_backlog(original)
        if result.events == original:
            return result

        self.journal.replace(result.events)
        self.queue.replace(result.events)
        return result

    def _persist_pending(self) -> None:
        self.journal.replace(self.queue.snapshot())
