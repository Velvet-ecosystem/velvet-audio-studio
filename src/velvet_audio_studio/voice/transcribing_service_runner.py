from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from velvet_audio_studio.runtime.capture_pipeline import ReliableRuntimeCycle
from velvet_audio_studio.runtime.service_runner import (
    AudioServiceState,
    AudioServiceStatus,
    ReliableAudioServiceRunner,
    ServiceIterationResult,
)
from velvet_audio_studio.voice.transcription_worker import (
    BoundedTranscriptionWorker,
    TranscriptionWorkerResult,
)


@dataclass(frozen=True)
class TranscribingServiceIterationResult:
    service: ServiceIterationResult
    transcription_results: tuple[TranscriptionWorkerResult, ...]
    transcription_cycles: tuple[ReliableRuntimeCycle, ...]

    @property
    def capture(self):
        return self.service.capture

    @property
    def state(self):
        return self.service.state

    @property
    def heartbeat_emitted(self) -> bool:
        return self.service.heartbeat_emitted


@dataclass(frozen=True)
class TranscribingServiceRunResult:
    boot_cycles: tuple[ReliableRuntimeCycle, ...]
    iterations: tuple[TranscribingServiceIterationResult, ...]
    shutdown_cycles: tuple[ReliableRuntimeCycle, ...]


class TranscribingAudioServiceRunner:
    """Add a bounded offline speech worker without blocking capture reads."""

    def __init__(
        self,
        service: ReliableAudioServiceRunner,
        worker: BoundedTranscriptionWorker,
        *,
        worker_stop_timeout_seconds: float = 10.0,
    ) -> None:
        if worker_stop_timeout_seconds <= 0:
            raise ValueError("worker_stop_timeout_seconds must be positive")
        self.service = service
        self.worker = worker
        self.worker_stop_timeout_seconds = worker_stop_timeout_seconds
        self._worker_started = False

    @property
    def state(self) -> AudioServiceState:
        return self.service.state

    @property
    def status(self) -> AudioServiceStatus:
        return self.service.status

    @property
    def pipeline(self):
        return self.service.pipeline

    @property
    def capture_source(self):
        return self.service.capture_source

    def boot(self) -> tuple[ReliableRuntimeCycle, ...]:
        cycles = list(self.service.boot())
        now_ns = self.service.clock_ns()
        events = self.worker.start(
            occurred_at_monotonic_ns=now_ns,
            packet_sequence=self.pipeline.supervisor.session.packet_sequence,
        )
        self._worker_started = True
        cycles.append(
            self.pipeline.publish_events(events, observed_at_monotonic_ns=now_ns)
        )
        extra_results, extra_cycles = self._drain_and_publish(now_ns)
        del extra_results
        cycles.extend(extra_cycles)
        return tuple(cycles)

    def run_once(self) -> TranscribingServiceIterationResult:
        service_result = self.service.run_once()
        now_ns = self.service.clock_ns()
        cycles: list[ReliableRuntimeCycle] = []
        results: list[TranscriptionWorkerResult] = []

        capture = service_result.capture
        if capture is not None and capture.voice_frontend is not None:
            utterance = capture.voice_frontend.completed_utterance
            if utterance is not None:
                submission = self.worker.submit(
                    utterance,
                    occurred_at_monotonic_ns=now_ns,
                    packet_sequence=self.pipeline.supervisor.session.packet_sequence,
                )
                cycles.append(
                    self.pipeline.publish_events(
                        submission.events,
                        observed_at_monotonic_ns=now_ns,
                    )
                )

        drained, drained_cycles = self._drain_and_publish(now_ns)
        results.extend(drained)
        cycles.extend(drained_cycles)
        return TranscribingServiceIterationResult(
            service=service_result,
            transcription_results=tuple(results),
            transcription_cycles=tuple(cycles),
        )

    def run(
        self,
        *,
        stop_requested: Callable[[], bool] = lambda: False,
        max_iterations: int | None = None,
    ) -> TranscribingServiceRunResult:
        if max_iterations is not None and max_iterations < 0:
            raise ValueError("max_iterations cannot be negative")
        boot_cycles = self.boot() if self.state is AudioServiceState.STOPPED else ()
        iterations: list[TranscribingServiceIterationResult] = []
        try:
            while not stop_requested():
                if max_iterations is not None and len(iterations) >= max_iterations:
                    break
                iterations.append(self.run_once())
        finally:
            shutdown_cycles = self.shutdown()
        return TranscribingServiceRunResult(
            boot_cycles=tuple(boot_cycles),
            iterations=tuple(iterations),
            shutdown_cycles=shutdown_cycles,
        )

    def shutdown(self) -> tuple[ReliableRuntimeCycle, ...]:
        cycles: list[ReliableRuntimeCycle] = []
        now_ns = self.service.clock_ns()
        if self._worker_started:
            results = self.worker.stop(
                timeout_seconds=self.worker_stop_timeout_seconds,
            )
            cycles.extend(self._publish_results(results, now_ns))
            self._worker_started = False
        cycles.extend(self.service.shutdown())
        return tuple(cycles)

    def _drain_and_publish(
        self,
        observed_at_monotonic_ns: int,
    ) -> tuple[tuple[TranscriptionWorkerResult, ...], tuple[ReliableRuntimeCycle, ...]]:
        results = self.worker.drain()
        return results, self._publish_results(results, observed_at_monotonic_ns)

    def _publish_results(
        self,
        results: tuple[TranscriptionWorkerResult, ...],
        observed_at_monotonic_ns: int,
    ) -> tuple[ReliableRuntimeCycle, ...]:
        events = tuple(event for result in results for event in result.events)
        if not events:
            return ()
        return (
            self.pipeline.publish_events(
                events,
                observed_at_monotonic_ns=observed_at_monotonic_ns,
            ),
        )
