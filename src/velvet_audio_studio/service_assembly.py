"""Assemble the configured Velvet audio service from explicit boundaries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns, sleep
from typing import Callable

from velvet_audio_studio.adapters.audio_injector_octo.capture_factory import (
    OctoCaptureResolution,
    resolve_octo_capture,
)
from velvet_audio_studio.capture.supervisor import CaptureSupervisor
from velvet_audio_studio.runtime.backlog_supervisor import DurableBacklogSupervisor
from velvet_audio_studio.runtime.capture_pipeline import ReliablePublishedCapturePipeline
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue
from velvet_audio_studio.runtime.publisher import RuntimeEventPublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal
from velvet_audio_studio.runtime.service_runner import (
    CaptureFrame,
    CaptureSource,
    ReliableAudioServiceRunner,
)
from velvet_audio_studio.service_config import AudioServiceConfig
from velvet_audio_studio.simulated.capture_source import (
    SimulatedCaptureSource,
    simulated_six_channel_frame,
)


CaptureResolver = Callable[..., OctoCaptureResolution]


@dataclass(frozen=True)
class AudioServiceAssembly:
    config: AudioServiceConfig
    capture_source: CaptureSource
    publisher: RuntimeEventPublisher
    journal: JsonlRetryJournal
    retry_queue: DurableOrderedRetryQueue
    capture_supervisor: CaptureSupervisor
    backlog_supervisor: DurableBacklogSupervisor
    pipeline: ReliablePublishedCapturePipeline
    runner: ReliableAudioServiceRunner
    capture_resolution: OctoCaptureResolution | None

    def describe(self) -> dict[str, object]:
        resolution = self.capture_resolution
        return {
            "node_id": self.config.studio.node_id,
            "capture_source": self.config.capture.source,
            "sample_rate_hz": self.config.capture.sample_rate_hz,
            "sample_format": self.config.capture.sample_format.value,
            "period_frames": self.config.capture.period_frames,
            "retry_journal": str(self.config.capture.retry_journal),
            "runtime_transport": self.config.network.transport,
            "runtime_endpoint": self.config.network.runtime_endpoint,
            "octo_accepted": resolution.accepted if resolution is not None else None,
            "alsa_device": (
                resolution.config.device
                if resolution is not None and resolution.config is not None
                else None
            ),
            "degraded_reasons": (
                resolution.degraded_reasons if resolution is not None else ()
            ),
        }


def build_audio_service(
    config: AudioServiceConfig,
    publisher: RuntimeEventPublisher,
    *,
    capture_resolver: CaptureResolver = resolve_octo_capture,
    simulated_items: Iterable[CaptureFrame | None | Exception] | None = None,
    clock_ns: Callable[[], int] = monotonic_ns,
    sleeper: Callable[[float], None] = sleep,
) -> AudioServiceAssembly:
    capture_source, resolution = _build_capture_source(
        config,
        capture_resolver=capture_resolver,
        simulated_items=simulated_items,
        clock_ns=clock_ns,
    )

    journal = JsonlRetryJournal(config.capture.retry_journal)
    retry_queue = DurableOrderedRetryQueue(
        journal,
        max_pending=config.capture.max_pending_runtime_events,
    )
    capture_supervisor = CaptureSupervisor()
    backlog_supervisor = DurableBacklogSupervisor(
        retry_queue,
        capacity_warning_ratio=config.capture.backlog_warning_ratio,
        max_age_ms=config.capture.backlog_max_age_ms,
    )
    pipeline = ReliablePublishedCapturePipeline(
        capture_supervisor,
        publisher,
        retry_queue,
        backlog_supervisor,
    )
    runner = ReliableAudioServiceRunner(
        pipeline,
        capture_source,
        heartbeat_interval_ms=config.capture.heartbeat_interval_ms,
        idle_poll_seconds=config.capture.idle_poll_seconds,
        clock_ns=clock_ns,
        sleeper=sleeper,
    )
    return AudioServiceAssembly(
        config=config,
        capture_source=capture_source,
        publisher=publisher,
        journal=journal,
        retry_queue=retry_queue,
        capture_supervisor=capture_supervisor,
        backlog_supervisor=backlog_supervisor,
        pipeline=pipeline,
        runner=runner,
        capture_resolution=resolution,
    )


def _build_capture_source(
    config: AudioServiceConfig,
    *,
    capture_resolver: CaptureResolver,
    simulated_items: Iterable[CaptureFrame | None | Exception] | None,
    clock_ns: Callable[[], int],
) -> tuple[CaptureSource, OctoCaptureResolution | None]:
    capture = config.capture
    if capture.source == "simulated":
        items = tuple(simulated_items) if simulated_items is not None else (
            _default_simulated_frame(
                sample_rate_hz=capture.sample_rate_hz,
            ),
        )
        return SimulatedCaptureSource(items, clock_ns=clock_ns), None

    resolution = capture_resolver(
        identity_terms=capture.identity_terms,
        pcm_device=capture.pcm_device,
        plug=capture.use_plughw,
        sample_rate_hz=capture.sample_rate_hz,
        period_frames=capture.period_frames,
        sample_format=capture.sample_format,
    )
    source = resolution.require_source(clock_ns=clock_ns)
    return source, resolution


def _default_simulated_frame(*, sample_rate_hz: int) -> CaptureFrame:
    """Provide two complete six-channel frames with a clear driver microphone."""
    return simulated_six_channel_frame(
        (
            0.20,
            0.10,
            0.04,
            0.03,
            0.08,
            0.00,
            -0.20,
            -0.10,
            -0.04,
            -0.03,
            -0.08,
            0.00,
        ),
        captured_at_monotonic_ns=0,
        sample_rate_hz=sample_rate_hz,
    )
