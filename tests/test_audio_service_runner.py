from pathlib import Path

from velvet_audio_studio.capture.supervisor import CaptureSupervisor, RuntimeAudioEvent
from velvet_audio_studio.runtime.backlog_supervisor import DurableBacklogSupervisor
from velvet_audio_studio.runtime.capture_pipeline import ReliablePublishedCapturePipeline
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue
from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal
from velvet_audio_studio.runtime.service_runner import (
    AudioServiceState,
    BackoffPolicy,
    CaptureFrame,
    ReliableAudioServiceRunner,
)


class FakeClock:
    def __init__(self, now_ns: int = 1_000_000_000) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns

    def advance_ms(self, milliseconds: int) -> None:
        self.now_ns += milliseconds * 1_000_000


class ScriptedCaptureSource:
    def __init__(
        self,
        reads: list[CaptureFrame | None | Exception] | None = None,
        *,
        open_failures: list[Exception] | None = None,
    ) -> None:
        self.reads = list(reads or [])
        self.open_failures = list(open_failures or [])
        self.open_count = 0
        self.close_count = 0
        self.is_open = False

    def open(self) -> None:
        self.open_count += 1
        if self.open_failures:
            raise self.open_failures.pop(0)
        self.is_open = True

    def read(self) -> CaptureFrame | None:
        if not self.is_open:
            raise RuntimeError("capture source is closed")
        if not self.reads:
            return None
        item = self.reads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.close_count += 1
        self.is_open = False


class TogglePublisher:
    def __init__(self, *, available: bool) -> None:
        self.available = available
        self.events: list[RuntimeAudioEvent] = []

    def publish(self, event: RuntimeAudioEvent) -> str:
        if not self.available:
            raise OSError("runtime unavailable")
        self.events.append(event)
        return f"receipt-{len(self.events)}"


def _frame(captured_ns: int = 1_100_000_000) -> CaptureFrame:
    return CaptureFrame(
        interleaved_samples=(
            0.2,
            0.1,
            0.0,
            0.0,
            0.0,
            0.0,
            -0.2,
            -0.1,
            0.0,
            0.0,
            0.0,
            0.0,
        ),
        captured_at_monotonic_ns=captured_ns,
    )


def _event(name: str, sequence: int, occurred_ns: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=occurred_ns,
        packet_sequence=sequence,
        payload={"sequence": sequence},
    )


def _runner(
    tmp_path: Path,
    source: ScriptedCaptureSource,
    publisher: object,
    clock: FakeClock,
    sleeps: list[float],
    *,
    heartbeat_interval_ms: int = 5_000,
    idle_poll_seconds: float = 0.0,
    backoff_policy: BackoffPolicy = BackoffPolicy(),
) -> tuple[ReliableAudioServiceRunner, DurableOrderedRetryQueue, JsonlRetryJournal]:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal)
    capture = CaptureSupervisor()
    backlog = DurableBacklogSupervisor(queue, max_age_ms=60_000)
    pipeline = ReliablePublishedCapturePipeline(
        capture,
        publisher,  # type: ignore[arg-type]
        queue,
        backlog,
    )
    runner = ReliableAudioServiceRunner(
        pipeline,
        source,
        heartbeat_interval_ms=heartbeat_interval_ms,
        idle_poll_seconds=idle_poll_seconds,
        backoff_policy=backoff_policy,
        clock_ns=clock,
        sleeper=sleeps.append,
    )
    return runner, queue, journal


def test_boot_replays_old_journal_before_service_start(tmp_path: Path) -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    publisher = InMemoryRuntimePublisher()
    source = ScriptedCaptureSource()
    runner, queue, journal = _runner(tmp_path, source, publisher, clock, sleeps)
    queue.enqueue((_event("audio.capture.degraded", 9, 900_000_000),))

    cycles = runner.boot()

    assert runner.state is AudioServiceState.RUNNING
    assert source.open_count == 1
    assert [event.event for event in publisher.events] == [
        "audio.capture.degraded",
        "audio.service.booting",
        "audio.capture.starting",
        "audio.service.running",
    ]
    assert len(cycles) == 3
    assert queue.status.queue.pending_count == 0
    assert journal.load() == ()
    assert sleeps == []


def test_capture_iteration_emits_heartbeat_after_interval(tmp_path: Path) -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    publisher = InMemoryRuntimePublisher()
    source = ScriptedCaptureSource([_frame(1_150_000_000)])
    runner, _queue, _journal = _runner(
        tmp_path,
        source,
        publisher,
        clock,
        sleeps,
        heartbeat_interval_ms=100,
    )
    runner.boot()
    clock.advance_ms(200)

    result = runner.run_once()

    assert result.capture is not None
    assert result.capture.capture.handoff.selected_logical_name == "driver_upper_mic"
    assert result.heartbeat_emitted is True
    assert runner.status.captured_packets == 1
    assert [event.event for event in publisher.events[-4:]] == [
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
        "audio.service.heartbeat",
    ]
    assert publisher.events[-1].payload["capture_session_state"] == "active"


def test_capture_failures_use_bounded_backoff_and_reopen(tmp_path: Path) -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    publisher = InMemoryRuntimePublisher()
    source = ScriptedCaptureSource(
        [
            OSError("first read failure"),
            OSError("second read failure"),
            OSError("third read failure"),
        ]
    )
    runner, _queue, _journal = _runner(
        tmp_path,
        source,
        publisher,
        clock,
        sleeps,
        backoff_policy=BackoffPolicy(
            initial_seconds=0.1,
            multiplier=2.0,
            maximum_seconds=0.2,
        ),
    )
    runner.boot()

    first = runner.run_once()
    recovered_first = runner.run_once()
    second = runner.run_once()
    recovered_second = runner.run_once()
    third = runner.run_once()

    assert first.state is AudioServiceState.BACKING_OFF
    assert recovered_first.state is AudioServiceState.RUNNING
    assert second.state is AudioServiceState.BACKING_OFF
    assert recovered_second.state is AudioServiceState.RUNNING
    assert third.state is AudioServiceState.BACKING_OFF
    assert sleeps == [0.1, 0.2, 0.2]
    assert source.open_count == 3
    assert source.close_count == 3
    assert runner.status.capture_failures == 3
    assert runner.status.consecutive_failures == 3
    assert [event.event for event in publisher.events].count(
        "audio.service.capture_error"
    ) == 3
    assert [event.event for event in publisher.events].count(
        "audio.service.capture_recovered"
    ) == 2


def test_boot_open_failure_recovers_without_losing_service_history(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    publisher = InMemoryRuntimePublisher()
    source = ScriptedCaptureSource(open_failures=[OSError("device not ready")])
    runner, _queue, _journal = _runner(
        tmp_path,
        source,
        publisher,
        clock,
        sleeps,
        backoff_policy=BackoffPolicy(initial_seconds=0.05, maximum_seconds=0.05),
    )

    boot_cycles = runner.boot()
    recovered = runner.run_once()

    assert runner.state is AudioServiceState.RUNNING
    assert len(boot_cycles) == 2
    assert recovered.state is AudioServiceState.RUNNING
    assert sleeps == [0.05]
    assert [event.event for event in publisher.events] == [
        "audio.service.booting",
        "audio.service.capture_error",
        "audio.service.backoff",
        "audio.capture.starting",
        "audio.service.capture_recovered",
    ]


def test_runtime_outage_journals_full_shutdown_order(tmp_path: Path) -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    publisher = TogglePublisher(available=False)
    source = ScriptedCaptureSource()
    runner, queue, journal = _runner(tmp_path, source, publisher, clock, sleeps)

    runner.boot()
    shutdown_cycles = runner.shutdown()

    assert runner.state is AudioServiceState.STOPPED
    assert source.close_count == 1
    assert len(shutdown_cycles) == 4
    assert [event.event for event in journal.load()] == [
        "audio.service.booting",
        "audio.capture.starting",
        "audio.service.running",
        "audio.service.stopping",
        "audio.capture.stopped",
        "audio.service.stopped",
    ]
    assert queue.status.queue.pending_count == 6


def test_run_stops_cleanly_at_iteration_limit(tmp_path: Path) -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    publisher = InMemoryRuntimePublisher()
    source = ScriptedCaptureSource([None, None])
    runner, _queue, _journal = _runner(
        tmp_path,
        source,
        publisher,
        clock,
        sleeps,
        idle_poll_seconds=0.01,
    )

    result = runner.run(max_iterations=2)

    assert len(result.iterations) == 2
    assert runner.state is AudioServiceState.STOPPED
    assert source.close_count == 1
    assert sleeps == [0.01, 0.01]
    assert [event.event for event in publisher.events[-3:]] == [
        "audio.service.stopping",
        "audio.capture.stopped",
        "audio.service.stopped",
    ]
