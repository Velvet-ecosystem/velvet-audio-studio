from pathlib import Path

from velvet_audio_studio.capture.supervisor import (
    CaptureSupervisor,
    RuntimeAudioEvent,
)
from velvet_audio_studio.runtime.backlog_supervisor import DurableBacklogSupervisor
from velvet_audio_studio.runtime.capture_pipeline import ReliablePublishedCapturePipeline
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue
from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal


def _samples(level: float = 0.2) -> tuple[float, ...]:
    return (
        level,
        level / 2,
        0.0,
        0.0,
        0.0,
        0.0,
        -level,
        -level / 2,
        0.0,
        0.0,
        0.0,
        0.0,
    )


def _event(name: str, sequence: int, occurred_ns: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=occurred_ns,
        packet_sequence=sequence,
        payload={"sequence": sequence},
    )


def _pipeline(
    tmp_path: Path,
    publisher: object,
    *,
    max_pending: int = 1024,
) -> tuple[
    ReliablePublishedCapturePipeline,
    DurableOrderedRetryQueue,
    JsonlRetryJournal,
]:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal, max_pending=max_pending)
    supervisor = CaptureSupervisor()
    backlog = DurableBacklogSupervisor(queue, max_age_ms=60_000)
    pipeline = ReliablePublishedCapturePipeline(
        supervisor,
        publisher,  # type: ignore[arg-type]
        queue,
        backlog,
    )
    return pipeline, queue, journal


def test_start_capture_and_stop_share_one_durable_ordered_loop(tmp_path: Path) -> None:
    publisher = InMemoryRuntimePublisher()
    pipeline, queue, journal = _pipeline(tmp_path, publisher)

    started = pipeline.start_and_publish(occurred_at_monotonic_ns=1_000_000_000)
    captured = pipeline.process_and_publish(
        _samples(),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )
    stopped = pipeline.stop_and_publish(occurred_at_monotonic_ns=1_030_000_000)

    assert [event.event for event in publisher.events] == [
        "audio.capture.starting",
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
        "audio.capture.stopped",
    ]
    assert started.pending_after == 0
    assert captured.runtime.pending_after == 0
    assert stopped.pending_after == 0
    assert captured.capture.handoff.selected_logical_name == "driver_upper_mic"
    assert queue.status.queue.pending_count == 0
    assert journal.load() == ()


def test_runtime_outage_persists_capture_then_replays_in_order(tmp_path: Path) -> None:
    class TogglePublisher:
        def __init__(self) -> None:
            self.available = False
            self.events: list[RuntimeAudioEvent] = []

        def publish(self, event: RuntimeAudioEvent) -> str:
            if not self.available:
                raise OSError("runtime unavailable")
            self.events.append(event)
            return f"receipt-{len(self.events)}"

    publisher = TogglePublisher()
    pipeline, queue, journal = _pipeline(tmp_path, publisher)
    pipeline.supervisor.start(occurred_at_monotonic_ns=1_000_000_000)

    failed = pipeline.process_and_publish(
        _samples(),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    assert failed.runtime.delivery.failed_count == 1
    assert failed.runtime.pending_after == 3
    assert [event.event for event in journal.load()] == [
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
    ]

    publisher.available = True
    replayed = pipeline.replay_pending(observed_at_monotonic_ns=1_030_000_000)

    assert [event.event for event in publisher.events] == [
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
    ]
    assert replayed.delivery.delivered_count == 3
    assert replayed.pending_after == 0
    assert queue.status.queue.pending_count == 0
    assert journal.load() == ()


def test_old_journal_events_are_delivered_before_fresh_capture(tmp_path: Path) -> None:
    publisher = InMemoryRuntimePublisher()
    pipeline, queue, _journal = _pipeline(tmp_path, publisher)
    queue.enqueue((_event("audio.capture.degraded", 9, 900_000_000),))
    pipeline.supervisor.start(occurred_at_monotonic_ns=1_000_000_000)

    pipeline.process_and_publish(
        _samples(),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    assert [event.event for event in publisher.events] == [
        "audio.capture.degraded",
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
    ]


def test_preflight_compaction_and_health_receipts_join_same_loop(
    tmp_path: Path,
) -> None:
    publisher = InMemoryRuntimePublisher()
    pipeline, queue, journal = _pipeline(tmp_path, publisher, max_pending=8)
    queue.enqueue(
        tuple(
            _event("audio.capture.packet", sequence, sequence * 100_000_000)
            for sequence in range(1, 7)
        )
    )
    pipeline.supervisor.start(occurred_at_monotonic_ns=1_000_000_000)

    result = pipeline.process_and_publish(
        _samples(),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    assert [event.event for event in publisher.events] == [
        "audio.capture.packet.summary",
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
        "audio.runtime_backlog.warning",
        "audio.runtime_backlog.compacted",
        "audio.runtime_backlog.recovered",
    ]
    assert [event.event for event in result.runtime.maintenance_events] == [
        "audio.runtime_backlog.warning",
        "audio.runtime_backlog.compacted",
        "audio.runtime_backlog.recovered",
    ]
    assert result.runtime.pending_after == 0
    assert journal.load() == ()
