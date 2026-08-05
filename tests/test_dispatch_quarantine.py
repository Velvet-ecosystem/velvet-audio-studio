from __future__ import annotations

from pathlib import Path

from velvet_audio_studio.runtime.acknowledgement_store import (
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.dispatch_quarantine import (
    QuarantinableIngressDispatchQueue,
    dispatch_failure_fingerprint,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    event_protocol_idempotency_key,
)
from velvet_audio_studio.runtime.ingress_dispatch import IngressDispatchStatus


class MutableClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _envelope(sequence: int) -> EventProtocolEnvelope:
    return EventProtocolEnvelope(
        event_type="audio.capture.packet",
        source_id="octo.capture.primary",
        sequence=sequence,
        occurred_at_monotonic_ns=sequence * 1_000,
        payload={"frames": 480},
    )


def _accepted_queue(
    tmp_path: Path,
) -> tuple[QuarantinableIngressDispatchQueue, MutableClock, str, str]:
    database = tmp_path / "runtime.sqlite3"
    clock = MutableClock()
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    first = _envelope(1)
    second = _envelope(2)
    first_key = event_protocol_idempotency_key(first)
    second_key = event_protocol_idempotency_key(second)
    store.acknowledge(first_key, first)
    clock.value += 1
    store.acknowledge(second_key, second)
    queue = QuarantinableIngressDispatchQueue(database, clock_ns=clock)
    return queue, clock, first_key, second_key


def test_identical_failure_evidence_accumulates_and_changed_failure_resets(
    tmp_path: Path,
) -> None:
    queue, clock, first_key, _second_key = _accepted_queue(tmp_path)

    first = queue.record_failure(
        first_key,
        "PermanentDispatchError: unsupported schema",
        poison_reason="unsupported schema",
    )
    clock.value += 1
    second = queue.record_failure(
        first_key,
        "PermanentDispatchError: unsupported schema",
        poison_reason="unsupported schema",
    )
    clock.value += 1
    changed = queue.record_failure(
        first_key,
        "RuntimeError: Court temporarily unavailable",
    )

    assert first.consecutive_count == 1
    assert second.consecutive_count == 2
    assert second.first_seen_unix_ns == first.first_seen_unix_ns
    assert changed.consecutive_count == 1
    assert changed.poison_reason is None
    assert changed.failure_fingerprint != second.failure_fingerprint


def test_quarantine_requires_matching_threshold_and_advances_ordered_lane(
    tmp_path: Path,
) -> None:
    queue, clock, first_key, second_key = _accepted_queue(tmp_path)
    error = "PermanentDispatchError: event type is permanently unsupported"
    fingerprint = dispatch_failure_fingerprint(error)

    queue.record_failure(
        first_key,
        error,
        poison_reason="event type is permanently unsupported",
    )
    assert queue.quarantine_if_threshold(
        first_key,
        failure_fingerprint=fingerprint,
        reason="event type is permanently unsupported",
        minimum_consecutive_failures=3,
    ) is None

    clock.value += 1
    queue.record_failure(
        first_key,
        error,
        poison_reason="event type is permanently unsupported",
    )
    clock.value += 1
    evidence = queue.record_failure(
        first_key,
        error,
        poison_reason="event type is permanently unsupported",
    )
    quarantine = queue.quarantine_if_threshold(
        first_key,
        failure_fingerprint=evidence.failure_fingerprint,
        reason="event type is permanently unsupported",
        minimum_consecutive_failures=3,
    )

    assert quarantine is not None
    assert quarantine.quarantine_receipt_id.startswith("runtime-quarantine-")
    assert quarantine.consecutive_failures == 3
    assert queue.quarantined_count() == 1
    first_record = queue.get(first_key)
    assert first_record is not None
    assert first_record.status is IngressDispatchStatus.PROCESSED
    assert first_record.downstream_receipt_id == quarantine.quarantine_receipt_id
    next_claim = queue.claim_next("worker-2", lease_seconds=5)
    assert next_claim is not None
    assert next_claim.idempotency_key == second_key


def test_quarantine_is_idempotent_after_receipt_is_committed(tmp_path: Path) -> None:
    queue, _clock, first_key, _second_key = _accepted_queue(tmp_path)
    error = "PermanentDispatchError: malformed fixed event"
    evidence = None
    for _ in range(3):
        evidence = queue.record_failure(
            first_key,
            error,
            poison_reason="malformed fixed event",
        )
    assert evidence is not None

    first = queue.quarantine_if_threshold(
        first_key,
        failure_fingerprint=evidence.failure_fingerprint,
        reason="malformed fixed event",
        minimum_consecutive_failures=3,
    )
    replay = queue.quarantine_if_threshold(
        first_key,
        failure_fingerprint=evidence.failure_fingerprint,
        reason="malformed fixed event",
        minimum_consecutive_failures=3,
    )

    assert first is not None
    assert replay == first
