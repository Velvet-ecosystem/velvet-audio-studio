from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic_ns, sleep
from typing import Callable, Protocol, Sequence

from velvet_audio_studio.capture.session import CaptureSessionState
from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.capture_pipeline import (
    ReliablePublishedCapturePipeline,
    ReliablePublishedCaptureResult,
    ReliableRuntimeCycle,
)


SERVICE_SOURCE_ID = "audio.service"


class CaptureSource(Protocol):
    """Boundary for a continuous multichannel capture device."""

    def open(self) -> None:
        ...

    def read(self) -> CaptureFrame | None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class CaptureFrame:
    interleaved_samples: tuple[float, ...]
    captured_at_monotonic_ns: int
    sample_rate_hz: int = 48_000
    muted_channels: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.captured_at_monotonic_ns < 0:
            raise ValueError("captured_at_monotonic_ns cannot be negative")
        if any(channel < 0 for channel in self.muted_channels):
            raise ValueError("muted channel indexes cannot be negative")


class AudioServiceState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BACKING_OFF = "backing_off"
    STOPPING = "stopping"


@dataclass(frozen=True)
class BackoffPolicy:
    initial_seconds: float = 0.25
    multiplier: float = 2.0
    maximum_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.initial_seconds <= 0:
            raise ValueError("initial_seconds must be positive")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.maximum_seconds < self.initial_seconds:
            raise ValueError("maximum_seconds cannot be below initial_seconds")

    def delay_for(self, consecutive_failures: int) -> float:
        if consecutive_failures <= 0:
            return 0.0
        delay = self.initial_seconds * self.multiplier ** (consecutive_failures - 1)
        return min(self.maximum_seconds, delay)


@dataclass(frozen=True)
class AudioServiceStatus:
    state: AudioServiceState
    iterations: int
    captured_packets: int
    capture_failures: int
    consecutive_failures: int
    pending_runtime_events: int
    last_capture_monotonic_ns: int | None
    last_heartbeat_monotonic_ns: int | None


@dataclass(frozen=True)
class ServiceIterationResult:
    state: AudioServiceState
    capture: ReliablePublishedCaptureResult | None
    runtime_cycles: tuple[ReliableRuntimeCycle, ...]
    slept_seconds: float
    capture_error: str | None
    heartbeat_emitted: bool


@dataclass(frozen=True)
class ServiceRunResult:
    boot_cycles: tuple[ReliableRuntimeCycle, ...]
    iterations: tuple[ServiceIterationResult, ...]
    shutdown_cycles: tuple[ReliableRuntimeCycle, ...]


class ReliableAudioServiceRunner:
    """Runs continuous capture through the durable Runtime delivery pipeline."""

    def __init__(
        self,
        pipeline: ReliablePublishedCapturePipeline,
        capture_source: CaptureSource,
        *,
        heartbeat_interval_ms: int = 5_000,
        idle_poll_seconds: float = 0.01,
        backoff_policy: BackoffPolicy = BackoffPolicy(),
        clock_ns: Callable[[], int] = monotonic_ns,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if heartbeat_interval_ms <= 0:
            raise ValueError("heartbeat_interval_ms must be positive")
        if idle_poll_seconds < 0:
            raise ValueError("idle_poll_seconds cannot be negative")
        self.pipeline = pipeline
        self.capture_source = capture_source
        self.heartbeat_interval_ns = heartbeat_interval_ms * 1_000_000
        self.idle_poll_seconds = idle_poll_seconds
        self.backoff_policy = backoff_policy
        self.clock_ns = clock_ns
        self.sleeper = sleeper

        self.state = AudioServiceState.STOPPED
        self.iterations = 0
        self.captured_packets = 0
        self.capture_failures = 0
        self.consecutive_failures = 0
        self.last_capture_monotonic_ns: int | None = None
        self.last_heartbeat_monotonic_ns: int | None = None
        self._source_open = False
        self._capture_started = False

    @property
    def status(self) -> AudioServiceStatus:
        return AudioServiceStatus(
            state=self.state,
            iterations=self.iterations,
            captured_packets=self.captured_packets,
            capture_failures=self.capture_failures,
            consecutive_failures=self.consecutive_failures,
            pending_runtime_events=self.pipeline.retry_queue.status.queue.pending_count,
            last_capture_monotonic_ns=self.last_capture_monotonic_ns,
            last_heartbeat_monotonic_ns=self.last_heartbeat_monotonic_ns,
        )

    def boot(self) -> tuple[ReliableRuntimeCycle, ...]:
        if self.state is not AudioServiceState.STOPPED:
            raise RuntimeError("audio service is already started")

        now_ns = self.clock_ns()
        self.state = AudioServiceState.STARTING
        cycles: list[ReliableRuntimeCycle] = [
            self.pipeline.publish_events(
                (self._event("audio.service.booting", now_ns),),
                observed_at_monotonic_ns=now_ns,
            )
        ]

        try:
            self.capture_source.open()
            self._source_open = True
        except Exception as exc:
            failure_cycle, _delay = self._capture_failure(exc, now_ns, phase="open")
            cycles.append(failure_cycle)
            return tuple(cycles)

        cycles.append(
            self.pipeline.start_and_publish(occurred_at_monotonic_ns=now_ns)
        )
        self._capture_started = True
        self.state = AudioServiceState.RUNNING
        self.last_heartbeat_monotonic_ns = now_ns
        cycles.append(
            self.pipeline.publish_events(
                (self._event("audio.service.running", now_ns),),
                observed_at_monotonic_ns=now_ns,
            )
        )
        return tuple(cycles)

    def run_once(self) -> ServiceIterationResult:
        if self.state in (AudioServiceState.STOPPED, AudioServiceState.STOPPING):
            raise RuntimeError("audio service is not running")

        self.iterations += 1
        now_ns = self.clock_ns()
        if self.state is AudioServiceState.BACKING_OFF:
            return self._recover_capture_source(now_ns)

        cycles: list[ReliableRuntimeCycle] = []
        capture_result: ReliablePublishedCaptureResult | None = None
        heartbeat_emitted = False
        slept_seconds = 0.0

        try:
            frame = self.capture_source.read()
        except Exception as exc:
            failure_cycle, slept_seconds = self._capture_failure(
                exc,
                now_ns,
                phase="read",
            )
            return ServiceIterationResult(
                state=self.state,
                capture=None,
                runtime_cycles=(failure_cycle,),
                slept_seconds=slept_seconds,
                capture_error=f"{type(exc).__name__}: {exc}",
                heartbeat_emitted=False,
            )

        if frame is None:
            if self.pipeline.retry_queue.status.queue.pending_count:
                cycles.append(
                    self.pipeline.replay_pending(
                        observed_at_monotonic_ns=now_ns,
                    )
                )
            if self.idle_poll_seconds:
                self.sleeper(self.idle_poll_seconds)
                slept_seconds = self.idle_poll_seconds
        else:
            capture_result = self.pipeline.process_and_publish(
                frame.interleaved_samples,
                sample_rate_hz=frame.sample_rate_hz,
                muted_channels=frame.muted_channels,
                captured_at_monotonic_ns=frame.captured_at_monotonic_ns,
                observed_at_monotonic_ns=now_ns,
            )
            self.captured_packets += 1
            self.last_capture_monotonic_ns = frame.captured_at_monotonic_ns
            self.consecutive_failures = 0
            cycles.append(capture_result.runtime)

        if self._heartbeat_due(now_ns):
            cycles.append(
                self.pipeline.publish_events(
                    (self._heartbeat_event(now_ns),),
                    observed_at_monotonic_ns=now_ns,
                )
            )
            self.last_heartbeat_monotonic_ns = now_ns
            heartbeat_emitted = True

        return ServiceIterationResult(
            state=self.state,
            capture=capture_result,
            runtime_cycles=tuple(cycles),
            slept_seconds=slept_seconds,
            capture_error=None,
            heartbeat_emitted=heartbeat_emitted,
        )

    def run(
        self,
        *,
        stop_requested: Callable[[], bool] = lambda: False,
        max_iterations: int | None = None,
    ) -> ServiceRunResult:
        if max_iterations is not None and max_iterations < 0:
            raise ValueError("max_iterations cannot be negative")

        boot_cycles = self.boot() if self.state is AudioServiceState.STOPPED else ()
        iterations: list[ServiceIterationResult] = []
        try:
            while not stop_requested():
                if max_iterations is not None and len(iterations) >= max_iterations:
                    break
                iterations.append(self.run_once())
        finally:
            shutdown_cycles = self.shutdown()

        return ServiceRunResult(
            boot_cycles=tuple(boot_cycles),
            iterations=tuple(iterations),
            shutdown_cycles=shutdown_cycles,
        )

    def shutdown(self) -> tuple[ReliableRuntimeCycle, ...]:
        if self.state is AudioServiceState.STOPPED:
            return ()

        now_ns = self.clock_ns()
        self.state = AudioServiceState.STOPPING
        cycles: list[ReliableRuntimeCycle] = [
            self.pipeline.publish_events(
                (self._event("audio.service.stopping", now_ns),),
                observed_at_monotonic_ns=now_ns,
            )
        ]

        close_error: str | None = None
        if self._source_open:
            try:
                self.capture_source.close()
            except Exception as exc:
                close_error = f"{type(exc).__name__}: {exc}"
                cycles.append(
                    self.pipeline.publish_events(
                        (
                            self._event(
                                "audio.service.capture_close_error",
                                now_ns,
                                {"error": close_error},
                            ),
                        ),
                        observed_at_monotonic_ns=now_ns,
                    )
                )
            finally:
                self._source_open = False

        if (
            self._capture_started
            and self.pipeline.supervisor.session.state
            is not CaptureSessionState.STOPPED
        ):
            cycles.append(
                self.pipeline.stop_and_publish(occurred_at_monotonic_ns=now_ns)
            )
        self._capture_started = False

        self.state = AudioServiceState.STOPPED
        cycles.append(
            self.pipeline.publish_events(
                (
                    self._event(
                        "audio.service.stopped",
                        now_ns,
                        {
                            "iterations": self.iterations,
                            "captured_packets": self.captured_packets,
                            "capture_failures": self.capture_failures,
                            "close_error": close_error,
                        },
                    ),
                ),
                observed_at_monotonic_ns=now_ns,
            )
        )

        if self.pipeline.retry_queue.status.queue.pending_count:
            cycles.append(
                self.pipeline.replay_pending(observed_at_monotonic_ns=now_ns)
            )
        return tuple(cycles)

    def _recover_capture_source(self, now_ns: int) -> ServiceIterationResult:
        try:
            self.capture_source.open()
            self._source_open = True
        except Exception as exc:
            failure_cycle, delay = self._capture_failure(exc, now_ns, phase="reopen")
            return ServiceIterationResult(
                state=self.state,
                capture=None,
                runtime_cycles=(failure_cycle,),
                slept_seconds=delay,
                capture_error=f"{type(exc).__name__}: {exc}",
                heartbeat_emitted=False,
            )

        cycles: list[ReliableRuntimeCycle] = []
        if not self._capture_started:
            cycles.append(
                self.pipeline.start_and_publish(occurred_at_monotonic_ns=now_ns)
            )
            self._capture_started = True

        self.state = AudioServiceState.RUNNING
        cycles.append(
            self.pipeline.publish_events(
                (
                    self._event(
                        "audio.service.capture_recovered",
                        now_ns,
                        {"consecutive_failures": self.consecutive_failures},
                    ),
                ),
                observed_at_monotonic_ns=now_ns,
            )
        )
        return ServiceIterationResult(
            state=self.state,
            capture=None,
            runtime_cycles=tuple(cycles),
            slept_seconds=0.0,
            capture_error=None,
            heartbeat_emitted=False,
        )

    def _capture_failure(
        self,
        exc: Exception,
        now_ns: int,
        *,
        phase: str,
    ) -> tuple[ReliableRuntimeCycle, float]:
        self.capture_failures += 1
        self.consecutive_failures += 1
        delay = self.backoff_policy.delay_for(self.consecutive_failures)
        self.state = AudioServiceState.BACKING_OFF

        if self._source_open:
            try:
                self.capture_source.close()
            except Exception:
                pass
            self._source_open = False

        cycle = self.pipeline.publish_events(
            (
                self._event(
                    "audio.service.capture_error",
                    now_ns,
                    {
                        "phase": phase,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "consecutive_failures": self.consecutive_failures,
                    },
                ),
                self._event(
                    "audio.service.backoff",
                    now_ns,
                    {
                        "delay_seconds": delay,
                        "consecutive_failures": self.consecutive_failures,
                    },
                ),
            ),
            observed_at_monotonic_ns=now_ns,
        )
        self.sleeper(delay)
        return cycle, delay

    def _heartbeat_due(self, now_ns: int) -> bool:
        if self.last_heartbeat_monotonic_ns is None:
            return True
        return now_ns - self.last_heartbeat_monotonic_ns >= self.heartbeat_interval_ns

    def _heartbeat_event(self, now_ns: int) -> RuntimeAudioEvent:
        status = self.status
        return self._event(
            "audio.service.heartbeat",
            now_ns,
            {
                "state": status.state.value,
                "iterations": status.iterations,
                "captured_packets": status.captured_packets,
                "capture_failures": status.capture_failures,
                "consecutive_failures": status.consecutive_failures,
                "pending_runtime_events": status.pending_runtime_events,
                "last_capture_monotonic_ns": status.last_capture_monotonic_ns,
                "capture_session_state": self.pipeline.supervisor.session.state.value,
            },
        )

    def _event(
        self,
        name: str,
        occurred_at_monotonic_ns: int,
        payload: dict[str, object] | None = None,
    ) -> RuntimeAudioEvent:
        return RuntimeAudioEvent(
            event=name,
            source_id=SERVICE_SOURCE_ID,
            occurred_at_monotonic_ns=occurred_at_monotonic_ns,
            packet_sequence=self.pipeline.supervisor.session.packet_sequence,
            payload={} if payload is None else payload,
        )
