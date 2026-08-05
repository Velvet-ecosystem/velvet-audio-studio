from pathlib import Path

import pytest

from velvet_audio_studio.runtime.acknowledgement_store import (
    AcknowledgementConflictError,
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    event_protocol_idempotency_key,
)


class TickClock:
    def __init__(self, *values: int) -> None:
        self.values = iter(values)

    def __call__(self) -> int:
        return next(self.values)


def _envelope(*, sequence: int = 7, state: str = "active") -> EventProtocolEnvelope:
    return EventProtocolEnvelope(
        event_type="audio.capture.active",
        source_id="octo.capture.primary",
        sequence=sequence,
        occurred_at_monotonic_ns=1_234_000_000,
        payload={"state": state, "frames": 480},
    )


def test_acknowledgement_persists_and_replays_stable_receipt(tmp_path: Path) -> None:
    database = tmp_path / "runtime-acks.sqlite3"
    envelope = _envelope()
    key = event_protocol_idempotency_key(envelope)
    first_store = SqliteAcknowledgementStore(
        database,
        clock_ns=TickClock(10_000, 20_000),
    )

    first = first_store.acknowledge(key, envelope)
    replay = first_store.acknowledge(key, envelope)

    assert first.duplicate is False
    assert first.duplicate_count == 0
    assert replay.duplicate is True
    assert replay.duplicate_count == 1
    assert replay.receipt_id == first.receipt_id
    assert replay.accepted_at_unix_ns == 10_000
    assert replay.last_seen_unix_ns == 20_000
    assert first_store.count() == 1

    reopened = SqliteAcknowledgementStore(database)
    stored = reopened.get(key)

    assert stored is not None
    assert stored.receipt_id == first.receipt_id
    assert stored.event_type == "audio.capture.active"
    assert stored.source_id == "octo.capture.primary"
    assert stored.sequence == 7
    assert stored.duplicate_count == 1
    assert reopened.count() == 1


def test_same_idempotency_key_cannot_claim_different_envelope(tmp_path: Path) -> None:
    store = SqliteAcknowledgementStore(tmp_path / "runtime-acks.sqlite3")
    original = _envelope(state="active")
    conflicting = _envelope(state="degraded")
    key = event_protocol_idempotency_key(original)
    store.acknowledge(key, original)

    with pytest.raises(AcknowledgementConflictError, match="different envelope bytes"):
        store.acknowledge(key, conflicting)

    stored = store.get(key)
    assert stored is not None
    assert stored.duplicate_count == 0
    assert store.count() == 1


def test_empty_idempotency_key_is_rejected(tmp_path: Path) -> None:
    store = SqliteAcknowledgementStore(tmp_path / "runtime-acks.sqlite3")

    with pytest.raises(ValueError, match="cannot be empty"):
        store.acknowledge("  ", _envelope())

    with pytest.raises(ValueError, match="cannot be empty"):
        store.get("")
