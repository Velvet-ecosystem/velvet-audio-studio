from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from velvet_audio_studio.capture.supervisor import (
    CaptureSupervisor,
    CaptureSupervisorResult,
)
from velvet_audio_studio.runtime.publisher import AudioRuntimeBridge, DeliveryBatch


@dataclass(frozen=True)
class PublishedCaptureResult:
    capture: CaptureSupervisorResult
    delivery: DeliveryBatch


class PublishedCapturePipeline:
    """Processes one capture buffer and publishes its ordered Runtime events."""

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
