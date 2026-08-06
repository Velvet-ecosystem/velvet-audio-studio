from pathlib import Path

import pytest

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.durable_retry_queue import (
    DurableOrderedRetryQueue,
    event_idempotency_key,
)
from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal, RetryJournalError


def _event(name: str, sequence: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=1_000_000_000 + sequence,
        packet_sequence=sequence,
        payload={"sequence": sequence},
    )


def test_queue_restores_pending_events_after_restart(tmp_path: Path) -> None:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal)
    queue.enqueue((_event("audio.capture.degraded", 4), _event("audio.capture.recovered", 5)))

    restored = DurableOrderedRetryQueue(journal)

    assert restored.status.queue.pending_count == 2
    assert [event.packet_sequence for event in restored.queue.snapshot()] == [4, 5]


def test_successful_delivery_removes_acknowledged_events_from_journal(tmp_path: Path) -> None:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal)
    queue.enqueue((_event("audio.capture.packet", 1), _event("audio.voice_input.ready", 1)))

    batch = queue.deliver(InMemoryRuntimePublisher())

    assert batch.delivered_count == 2
    assert queue.status.queue.pending_count == 0
    assert journal.load() == ()


def test_duplicate_event_is_not_enqueued_twice(tmp_path: Path) -> None:
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal)
    event = _event("audio.capture.packet", 8)

    queue.enqueue((event,))
    queue.enqueue((event,))

    assert queue.status.queue.pending_count == 1
    assert len(event_idempotency_key(event)) == 64


def test_corrupt_journal_fails_closed_with_line_number(tmp_path: Path) -> None:
    path = tmp_path / "audio-retry.jsonl"
    path.write_text('{"event":"ok"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(RetryJournalError, match="line 1"):
        JsonlRetryJournal(path).load()
