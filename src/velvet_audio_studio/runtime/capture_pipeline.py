from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import Sequence

from velvet_audio_studio.capture.supervisor import (
    CaptureSupervisor,
    CaptureSupervisorResult,
    RuntimeAudioEvent,
)
from velvet_audio_studio.runtime.backlog_supervisor import (
    BacklogMaintenanceResult,
    DurableBacklogSupervisor,
)
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue
from velvet_audio_studio.runtime.publisher import (
    AudioRuntimeBridge,
    DeliveryBatch,
    RuntimeEventPublisher,
)


@dataclass(frozen=True)
class PublishedCaptureResult:
    capture: CaptureSupervisorResult
    delivery: DeliveryBatch


class PublishedCapturePipeline:
    """Processes one capture buffer and publishes its ordered Runtime events.

    This direct pipeline remains useful for small simulations. Vehicle deployments
    should use ReliablePublishedCapturePipeline so failed delivery is journaled.
    """

    def __init__(
        self,
        supervisor: CaptureSupervisor,
        runtime_bridge: AudioRuntimeBridge,
    ) -> None:
        self.supervisor = supervisor
        self.runtime_bridge = runtime_bridge

    def process_and_publish(
        self,
        interleaved_samples: Sequence[float],
        *,
        sample_rate_hz: int = 48_000,
        muted_channels: frozenset[int] = frozenset(),
        captured_at_monotonic_ns: int | None = None,
        observed_at_monotonic_ns: int | None = None,
    ) -> PublishedCaptureResult:
        capture = self.supervisor.process(
            interleaved_samples,
            sample_rate_hz=sample_rate_hz,
            muted_channels=muted_channels,
            captured_at_monotonic_ns=captured_at_monotonic_ns,
            observed_at_monotonic_ns=observed_at_monotonic_ns,
        )
        delivery = self.runtime_bridge.deliver(capture.events)
        return PublishedCaptureResult(capture=capture, delivery=delivery)


@dataclass(frozen=True)
class ReliableRuntimeCycle:
    """One ordered journal, delivery, and backlog-maintenance cycle."""

    maintenance_before: BacklogMaintenanceResult
    maintenance_after: BacklogMaintenanceResult
    maintenance_events: tuple[RuntimeAudioEvent, ...]
    delivery: DeliveryBatch
    pending_after: int


@dataclass(frozen=True)
class ReliablePublishedCaptureResult:
    capture: CaptureSupervisorResult
    runtime: ReliableRuntimeCycle


class ReliablePublishedCapturePipeline:
    """Continuous capture-to-Runtime loop with durable ordered recovery.

    New audio events are appended behind any older journaled events. Delivery
    always starts at the oldest pending event and stops at the first failure.
    Backlog health events are appended after the audio events they describe.
    """

    def __init__(
        self,
        supervisor: CaptureSupervisor,
        publisher: RuntimeEventPublisher,
        retry_queue: DurableOrderedRetryQueue,
        backlog_supervisor: DurableBacklogSupervisor | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.publisher = publisher
        self.retry_queue = retry_queue
        self.backlog_supervisor = backlog_supervisor or DurableBacklogSupervisor(
            retry_queue
        )
        if self.backlog_supervisor.queue is not retry_queue:
            raise ValueError("backlog supervisor must manage the pipeline retry queue")

    def start_and_publish(
        self,
        *,
        occurred_at_monotonic_ns: int | None = None,
    ) -> ReliableRuntimeCycle:
        occurred_ns = (
            monotonic_ns()
            if occurred_at_monotonic_ns is None
            else occurred_at_monotonic_ns
        )
        event = self.supervisor.start(occurred_at_monotonic_ns=occurred_ns)
        return self._publish_cycle((event,), observed_at_monotonic_ns=occurred_ns)

    def process_and_publish(
        self,
        interleaved_samples: Sequence[float],
        *,
        sample_rate_hz: int = 48_000,
        muted_channels: frozenset[int] = frozenset(),
        captured_at_monotonic_ns: int | None = None,
        observed_at_monotonic_ns: int | None = None,
    ) -> ReliablePublishedCaptureResult:
        observed_ns = (
            monotonic_ns()
            if observed_at_monotonic_ns is None
            else observed_at_monotonic_ns
        )
        capture = self.supervisor.process(
            interleaved_samples,
            sample_rate_hz=sample_rate_hz,
            muted_channels=muted_channels,
            captured_at_monotonic_ns=captured_at_monotonic_ns,
            observed_at_monotonic_ns=observed_ns,
        )
        runtime = self._publish_cycle(
            capture.events,
            observed_at_monotonic_ns=observed_ns,
        )
        return ReliablePublishedCaptureResult(capture=capture, runtime=runtime)

    def stop_and_publish(
        self,
        *,
        occurred_at_monotonic_ns: int | None = None,
    ) -> ReliableRuntimeCycle:
        occurred_ns = (
            monotonic_ns()
            if occurred_at_monotonic_ns is None
            else occurred_at_monotonic_ns
        )
        event = self.supervisor.stop(occurred_at_monotonic_ns=occurred_ns)
        return self._publish_cycle((event,), observed_at_monotonic_ns=occurred_ns)

    def replay_pending(
        self,
        *,
        observed_at_monotonic_ns: int | None = None,
    ) -> ReliableRuntimeCycle:
        """Retry journaled events without requiring a new microphone buffer."""
        observed_ns = (
            monotonic_ns()
            if observed_at_monotonic_ns is None
            else observed_at_monotonic_ns
        )
        return self._publish_cycle((), observed_at_monotonic_ns=observed_ns)

    def _publish_cycle(
        self,
        new_events: Sequence[RuntimeAudioEvent],
        *,
        observed_at_monotonic_ns: int,
    ) -> ReliableRuntimeCycle:
        # Compact and assess older backlog before appending current events. The
        # health receipts themselves are appended later, after the evidence they
        # describe, so they cannot jump ahead of older capture history.
        maintenance_before = self.backlog_supervisor.maintain(
            observed_at_monotonic_ns=observed_at_monotonic_ns
        )

        self.retry_queue.enqueue(new_events)
        primary_delivery = self.retry_queue.deliver(self.publisher)

        maintenance_after = self.backlog_supervisor.maintain(
            observed_at_monotonic_ns=observed_at_monotonic_ns
        )
        maintenance_events = (
            maintenance_before.events + maintenance_after.events
        )
        self.retry_queue.enqueue(maintenance_events)

        follow_up_delivery = DeliveryBatch(())
        if primary_delivery.failed_count == 0 and maintenance_events:
            follow_up_delivery = self.retry_queue.deliver(self.publisher)

        delivery = DeliveryBatch(
            primary_delivery.receipts + follow_up_delivery.receipts
        )
        return ReliableRuntimeCycle(
            maintenance_before=maintenance_before,
            maintenance_after=maintenance_after,
            maintenance_events=maintenance_events,
            delivery=delivery,
            pending_after=self.retry_queue.status.queue.pending_count,
        )
