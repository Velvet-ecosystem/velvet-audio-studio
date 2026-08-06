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
from velvet_audio_studio.voice.front_end import (
    LocalVoiceFrontEnd,
    LocalVoiceFrontEndResult,
)


@dataclass(frozen=True)
class PublishedCaptureResult:
    capture: CaptureSupervisorResult
    delivery: DeliveryBatch
    voice_frontend: LocalVoiceFrontEndResult | None = None


class PublishedCapturePipeline:
    """Processes one capture buffer and publishes its ordered Runtime events.

    This direct pipeline remains useful for small simulations. Vehicle deployments
    should use ReliablePublishedCapturePipeline so failed delivery is journaled.
    """

    def __init__(
        self,
        supervisor: CaptureSupervisor,
        runtime_bridge: AudioRuntimeBridge,
        voice_frontend: LocalVoiceFrontEnd | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.runtime_bridge = runtime_bridge
        self.voice_frontend = voice_frontend

    def process_and_publish(
        self,
        interleaved_samples: Sequence[float],
        *,
        sample_rate_hz: int = 48_000,
        muted_channels: frozenset[int] = frozenset(),
        captured_at_monotonic_ns: int | None = None,
        observed_at_monotonic_ns: int | None = None,
    ) -> PublishedCaptureResult:
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
        voice = self._process_voice_frontend(
            capture,
            sample_rate_hz=sample_rate_hz,
            observed_at_monotonic_ns=observed_ns,
        )
        events = capture.events + (() if voice is None else voice.events)
        delivery = self.runtime_bridge.deliver(events)
        return PublishedCaptureResult(
            capture=capture,
            delivery=delivery,
            voice_frontend=voice,
        )

    def _process_voice_frontend(
        self,
        capture: CaptureSupervisorResult,
        *,
        sample_rate_hz: int,
        observed_at_monotonic_ns: int,
    ) -> LocalVoiceFrontEndResult | None:
        if self.voice_frontend is None:
            return None
        return self.voice_frontend.process(
            capture.handoff,
            sample_rate_hz=sample_rate_hz,
            occurred_at_monotonic_ns=observed_at_monotonic_ns,
            packet_sequence=self.supervisor.session.packet_sequence,
        )


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
    voice_frontend: LocalVoiceFrontEndResult | None = None


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
        voice_frontend: LocalVoiceFrontEnd | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.publisher = publisher
        self.retry_queue = retry_queue
        self.backlog_supervisor = backlog_supervisor or DurableBacklogSupervisor(
            retry_queue
        )
        self.voice_frontend = voice_frontend
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
        voice = self._process_voice_frontend(
            capture,
            sample_rate_hz=sample_rate_hz,
            observed_at_monotonic_ns=observed_ns,
        )
        runtime_events = capture.events + (() if voice is None else voice.events)
        runtime = self._publish_cycle(
            runtime_events,
            observed_at_monotonic_ns=observed_ns,
        )
        return ReliablePublishedCaptureResult(
            capture=capture,
            runtime=runtime,
            voice_frontend=voice,
        )

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
        voice_events: tuple[RuntimeAudioEvent, ...] = ()
        if self.voice_frontend is not None:
            voice_events = self.voice_frontend.stop(
                occurred_at_monotonic_ns=occurred_ns,
                packet_sequence=self.supervisor.session.packet_sequence,
            )
        event = self.supervisor.stop(occurred_at_monotonic_ns=occurred_ns)
        return self._publish_cycle(
            voice_events + (event,),
            observed_at_monotonic_ns=occurred_ns,
        )

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

    def publish_events(
        self,
        events: Sequence[RuntimeAudioEvent],
        *,
        observed_at_monotonic_ns: int | None = None,
    ) -> ReliableRuntimeCycle:
        """Publish non-capture service events through the same durable ordering path."""
        observed_ns = (
            monotonic_ns()
            if observed_at_monotonic_ns is None
            else observed_at_monotonic_ns
        )
        return self._publish_cycle(events, observed_at_monotonic_ns=observed_ns)

    def _process_voice_frontend(
        self,
        capture: CaptureSupervisorResult,
        *,
        sample_rate_hz: int,
        observed_at_monotonic_ns: int,
    ) -> LocalVoiceFrontEndResult | None:
        if self.voice_frontend is None:
            return None
        return self.voice_frontend.process(
            capture.handoff,
            sample_rate_hz=sample_rate_hz,
            occurred_at_monotonic_ns=observed_at_monotonic_ns,
            packet_sequence=self.supervisor.session.packet_sequence,
        )

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
