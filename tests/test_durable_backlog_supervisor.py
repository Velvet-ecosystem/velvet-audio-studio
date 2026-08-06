from pathlib import Path

import pytest

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.backlog_supervisor import DurableBacklogSupervisor
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal


def _event(name: str, sequence: int, occurred_ns: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=occurred_ns,
        packet_sequence=sequence,
        payload={"frames": 64, "sequence": sequence},
    )


def test_warning_compacts_journal_and_reports_recovery(tmp_path: Path) -> None:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal, max_pending=4)
    queue.enqueue(
        (
            _event("audio.capture.packet", 1, 1_000_000_000),
            _event("audio.capture.packet", 2, 2_000_000_000),
            _event("audio.capture.packet", 3, 3_000_000_000),
        )
    )
    supervisor = DurableBacklogSupervisor(queue, max_age_ms=60_000)

    result = supervisor.maintain(observed_at_monotonic_ns=4_000_000_000)

    assert result.health_before.state == "warning"
    assert result.health_after.state == "healthy"
    assert result.compaction is not None
    assert result.compaction.removed_count == 2
    assert [event.event for event in result.events] == [
        "audio.runtime_backlog.warning",
        "audio.runtime_backlog.compacted",
        "audio.runtime_backlog.recovered",
    ]
    assert [event.event for event in journal.load()] == [
        "audio.capture.packet.summary",
    ]

    restored = DurableOrderedRetryQueue(journal, max_pending=4)
    assert [event.event for event in restored.queue.snapshot()] == [
        "audio.capture.packet.summary",
    ]


def test_unchanged_warning_does_not_repeat_health_event(tmp_path: Path) -> None:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal, max_pending=4)
    queue.enqueue(
        (
            _event("audio.capture.starting", 0, 1_000_000_000),
            _event("audio.capture.active", 1, 2_000_000_000),
            _event("audio.capture.degraded", 2, 3_000_000_000),
        )
    )
    supervisor = DurableBacklogSupervisor(queue, max_age_ms=60_000)

    first = supervisor.maintain(observed_at_monotonic_ns=4_000_000_000)
    second = supervisor.maintain(observed_at_monotonic_ns=5_000_000_000)

    assert [event.event for event in first.events] == [
        "audio.runtime_backlog.warning",
    ]
    assert second.events == ()


def test_warning_escalates_to_critical_once(tmp_path: Path) -> None:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal, max_pending=4)
    queue.enqueue(
        (
            _event("audio.capture.starting", 0, 1_000_000_000),
            _event("audio.capture.active", 1, 2_000_000_000),
            _event("audio.capture.degraded", 2, 3_000_000_000),
        )
    )
    supervisor = DurableBacklogSupervisor(queue, max_age_ms=60_000)
    supervisor.maintain(observed_at_monotonic_ns=4_000_000_000)

    queue.enqueue((_event("audio.voice_input.degraded", 2, 4_000_000_000),))
    critical = supervisor.maintain(observed_at_monotonic_ns=5_000_000_000)
    repeated = supervisor.maintain(observed_at_monotonic_ns=6_000_000_000)

    assert [event.event for event in critical.events] == [
        "audio.runtime_backlog.critical",
    ]
    assert repeated.events == ()


def test_failed_journal_compaction_leaves_memory_and_disk_unchanged(
    tmp_path: Path,
) -> None:
    class FailingJournal(JsonlRetryJournal):
        fail_replace = False

        def replace(self, events: object) -> None:
            if self.fail_replace:
                raise OSError("simulated storage failure")
            super().replace(events)  # type: ignore[arg-type]

    journal = FailingJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal, max_pending=4)
    original = (
        _event("audio.capture.packet", 1, 1_000_000_000),
        _event("audio.capture.packet", 2, 2_000_000_000),
    )
    queue.enqueue(original)
    journal.fail_replace = True

    with pytest.raises(OSError, match="simulated storage failure"):
        queue.compact_and_persist()

    assert queue.queue.snapshot() == original
    journal.fail_replace = False
    assert journal.load() == original


def test_duplicate_inside_one_enqueue_batch_is_suppressed(tmp_path: Path) -> None:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal)
    event = _event("audio.capture.packet", 8, 8_000_000_000)

    queue.enqueue((event, event))

    assert queue.status.queue.pending_count == 1
    assert journal.load() == (event,)
