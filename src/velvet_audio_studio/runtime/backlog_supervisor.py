from __future__ import annotations

from dataclasses import dataclass

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.backlog_policy import BacklogHealth, CompactionResult
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue


BACKLOG_SOURCE_ID = "audio.runtime_backlog"


@dataclass(frozen=True)
class BacklogMaintenanceResult:
    health_before: BacklogHealth
    health_after: BacklogHealth
    compaction: CompactionResult | None
    events: tuple[RuntimeAudioEvent, ...]


class BacklogHealthMonitor:
    """Emits one Runtime event whenever backlog health changes state."""

    def __init__(self) -> None:
        self.state = "healthy"

    def observe(
        self,
        health: BacklogHealth,
        *,
        occurred_at_monotonic_ns: int,
        packet_sequence: int,
    ) -> tuple[RuntimeAudioEvent, ...]:
        if health.state == self.state:
            return ()

        previous_state = self.state
        self.state = health.state
        event_name = {
            "warning": "audio.runtime_backlog.warning",
            "critical": "audio.runtime_backlog.critical",
            "healthy": "audio.runtime_backlog.recovered",
        }.get(health.state)
        if event_name is None:
            raise ValueError(f"unsupported backlog health state: {health.state}")

        return (
            RuntimeAudioEvent(
                event=event_name,
                source_id=BACKLOG_SOURCE_ID,
                occurred_at_monotonic_ns=occurred_at_monotonic_ns,
                packet_sequence=packet_sequence,
                payload={
                    "previous_state": previous_state,
                    "current_state": health.state,
                    "pending_count": health.pending_count,
                    "capacity_ratio": health.capacity_ratio,
                    "oldest_age_ms": health.oldest_age_ms,
                    "warning_reasons": health.warning_reasons,
                },
            ),
        )


class DurableBacklogSupervisor:
    """Assesses, compacts, persists, and reports Runtime backlog health."""

    def __init__(
        self,
        queue: DurableOrderedRetryQueue,
        *,
        capacity_warning_ratio: float = 0.75,
        max_age_ms: int = 30_000,
    ) -> None:
        self.queue = queue
        self.capacity_warning_ratio = capacity_warning_ratio
        self.max_age_ms = max_age_ms
        self.monitor = BacklogHealthMonitor()

    def maintain(
        self,
        *,
        observed_at_monotonic_ns: int,
    ) -> BacklogMaintenanceResult:
        health_before = self._health(observed_at_monotonic_ns)
        packet_sequence = self.queue.status.queue.newest_packet_sequence or 0
        events = list(
            self.monitor.observe(
                health_before,
                occurred_at_monotonic_ns=observed_at_monotonic_ns,
                packet_sequence=packet_sequence,
            )
        )

        compaction: CompactionResult | None = None
        health_after = health_before
        if health_before.state != "healthy":
            compaction = self.queue.compact_and_persist()
            health_after = self._health(observed_at_monotonic_ns)

            if compaction.removed_count > 0:
                events.append(
                    RuntimeAudioEvent(
                        event="audio.runtime_backlog.compacted",
                        source_id=BACKLOG_SOURCE_ID,
                        occurred_at_monotonic_ns=observed_at_monotonic_ns,
                        packet_sequence=packet_sequence,
                        payload={
                            "removed_count": compaction.removed_count,
                            "summary_count": compaction.summary_count,
                            "pending_before": health_before.pending_count,
                            "pending_after": health_after.pending_count,
                            "journal_path": self.queue.status.journal_path,
                        },
                    )
                )

            events.extend(
                self.monitor.observe(
                    health_after,
                    occurred_at_monotonic_ns=observed_at_monotonic_ns,
                    packet_sequence=packet_sequence,
                )
            )

        return BacklogMaintenanceResult(
            health_before=health_before,
            health_after=health_after,
            compaction=compaction,
            events=tuple(events),
        )

    def _health(self, observed_at_monotonic_ns: int) -> BacklogHealth:
        return self.queue.health(
            observed_at_monotonic_ns=observed_at_monotonic_ns,
            capacity_warning_ratio=self.capacity_warning_ratio,
            max_age_ms=self.max_age_ms,
        )
