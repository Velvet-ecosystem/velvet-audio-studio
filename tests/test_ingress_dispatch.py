from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from velvet_audio_studio.runtime.acknowledgement_store import (
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    event_protocol_idempotency_key,
)
from velvet_audio_studio.runtime.ingress_dispatch import (
    DispatchCycleState,
    DurableIngressDispatcher,
    IngressClaimLostError,
    IngressDispatchStatus,
    SqliteIngressDispatchQueue,
)


class MutableClock:
    def __init__(self, now_ns: int = 1_000_000_000) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns

    def advance_ns(self, amount: int) -> None:
        self.now_ns += amount

    def advance_seconds(self, amount: float) -> None:
        self.advance_ns(int(amount * 1_000_000_000))


def _envelope(event_type: str, sequence: int) -> EventProtocolEnvelope:
    return EventProtocolEnvelope(
        event_type=event_type,
        source_id="octo.capture.primary",
        sequence=sequence,
        occurred_at_monotonic_ns=sequence * 1_000_000,
        payload={"sequence": sequence},
    )


def _accept(
    store: SqliteAcknowledgementStore,
    event_type: str,
    sequence: int,
) -> tuple[EventProtocolEnvelope, str, str]:
    envelope = _envelope(event_type, sequence)
    key = event_protocol_idempotency_key(envelope)
    acknowledgement = store.acknowledge(key, envelope)
    return envelope, key, acknowledgement.receipt_id


def test_acceptance_seeds_pending_dispatch_identity(tmp_path: Path) -> None:
    clock = MutableClock()
    database = tmp_path / "runtime.sqlite3"
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    _envelope_value, key, receipt_id = _accept(
        store,
        "audio.capture.degraded",
        1,
    )

    queue = SqliteIngressDispatchQueue(database, clock_ns=clock)
    record = queue.get(key)

    assert record is not None
    assert record.status is IngressDispatchStatus.PENDING
    assert record.dispatch_id == receipt_id.replace(
        "runtime-receipt-",
        "runtime-dispatch-",
        1,
    )
    assert record.ingress_receipt_id == receipt_id
    assert record.attempt_count == 0
    assert queue.stats().pending == 1


def test_live_oldest_claim_blocks_every_later_event(tmp_path: Path) -> None:
    clock = MutableClock()
    database = tmp_path / "runtime.sqlite3"
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    _first, first_key, _first_receipt = _accept(
        store,
        "audio.capture.degraded",
        1,
    )
    clock.advance_ns(1)
    _second, second_key, _second_receipt = _accept(
        store,
        "audio.capture.recovered",
        2,
    )
    queue = SqliteIngressDispatchQueue(database, clock_ns=clock)

    first_claim = queue.claim_next("worker-a", lease_seconds=10)
    blocked = queue.claim_next("worker-b", lease_seconds=10)

    assert first_claim is not None
    assert first_claim.idempotency_key == first_key
    assert blocked is None
    queue.complete(first_claim.claim_token, "court-receipt-1")

    second_claim = queue.claim_next("worker-b", lease_seconds=10)
    assert second_claim is not None
    assert second_claim.idempotency_key == second_key


def test_expired_claim_is_reclaimed_and_stale_worker_cannot_complete(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    database = tmp_path / "runtime.sqlite3"
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    _accepted, key, _receipt = _accept(store, "audio.capture.active", 1)
    queue = SqliteIngressDispatchQueue(database, clock_ns=clock)

    first = queue.claim_next("worker-a", lease_seconds=1)
    assert first is not None
    clock.advance_seconds(1.1)
    assert queue.stats().expired_claims == 1

    second = queue.claim_next("worker-b", lease_seconds=5)
    assert second is not None
    assert second.idempotency_key == key
    assert second.dispatch_id == first.dispatch_id
    assert second.claim_token != first.claim_token
    assert second.attempt_count == 2

    with pytest.raises(IngressClaimLostError):
        queue.complete(first.claim_token, "late-court-receipt")

    completed = queue.complete(second.claim_token, "court-receipt-2")
    assert completed.status is IngressDispatchStatus.PROCESSED
    assert completed.downstream_receipt_id == "court-receipt-2"


def test_renewed_claim_keeps_order_gate_closed(tmp_path: Path) -> None:
    clock = MutableClock()
    database = tmp_path / "runtime.sqlite3"
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    _accept(store, "audio.capture.degraded", 1)
    clock.advance_ns(1)
    _accept(store, "audio.capture.recovered", 2)
    queue = SqliteIngressDispatchQueue(database, clock_ns=clock)

    claim = queue.claim_next("worker-a", lease_seconds=1)
    assert claim is not None
    clock.advance_seconds(0.5)
    renewed_expiry = queue.renew(claim.claim_token, lease_seconds=2)
    clock.advance_seconds(0.75)

    assert renewed_expiry > clock.now_ns
    assert queue.claim_next("worker-b", lease_seconds=1) is None


@dataclass
class RecordingHandler:
    failures_remaining: int = 0
    clock: MutableClock | None = None
    expire_first_call: bool = False

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        self.calls.append((envelope.event_type, dispatch_id, ingress_receipt_id))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("Court temporarily unavailable")
        if self.expire_first_call and len(self.calls) == 1:
            assert self.clock is not None
            self.clock.advance_seconds(2)
        return "court-" + dispatch_id


def test_dispatcher_releases_failure_and_reuses_stable_dispatch_id(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    database = tmp_path / "runtime.sqlite3"
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    _accepted, key, _receipt = _accept(store, "audio.voice_input.ready", 1)
    queue = SqliteIngressDispatchQueue(database, clock_ns=clock)
    handler = RecordingHandler(failures_remaining=1)
    dispatcher = DurableIngressDispatcher(
        queue,
        handler,
        worker_id="court-worker",
        lease_seconds=10,
    )

    failed = dispatcher.process_one()
    released = queue.get(key)
    retried = dispatcher.process_one()
    completed = queue.get(key)

    assert failed.state is DispatchCycleState.RETRY
    assert released is not None
    assert released.status is IngressDispatchStatus.PENDING
    assert "Court temporarily unavailable" in (released.last_error or "")
    assert retried.state is DispatchCycleState.PROCESSED
    assert completed is not None
    assert completed.status is IngressDispatchStatus.PROCESSED
    assert completed.attempt_count == 2
    assert handler.calls[0][1] == handler.calls[1][1]
    assert completed.downstream_receipt_id == "court-" + handler.calls[1][1]


def test_crash_gap_retries_same_dispatch_identity_after_claim_loss(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    database = tmp_path / "runtime.sqlite3"
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    _accepted, key, _receipt = _accept(store, "audio.service.heartbeat", 1)
    queue = SqliteIngressDispatchQueue(database, clock_ns=clock)
    handler = RecordingHandler(clock=clock, expire_first_call=True)
    dispatcher = DurableIngressDispatcher(
        queue,
        handler,
        worker_id="court-worker",
        lease_seconds=1,
    )

    lost = dispatcher.process_one()
    recovered = dispatcher.process_one()
    record = queue.get(key)

    assert lost.state is DispatchCycleState.CLAIM_LOST
    assert lost.downstream_receipt_id is not None
    assert recovered.state is DispatchCycleState.PROCESSED
    assert handler.calls[0][1] == handler.calls[1][1]
    assert record is not None
    assert record.status is IngressDispatchStatus.PROCESSED
    assert record.attempt_count == 2


def test_drain_available_preserves_acceptance_order(tmp_path: Path) -> None:
    clock = MutableClock()
    database = tmp_path / "runtime.sqlite3"
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    _accept(store, "audio.capture.degraded", 1)
    clock.advance_ns(1)
    _accept(store, "audio.capture.recovered", 2)
    queue = SqliteIngressDispatchQueue(database, clock_ns=clock)
    handler = RecordingHandler()
    dispatcher = DurableIngressDispatcher(
        queue,
        handler,
        worker_id="court-worker",
    )

    results = dispatcher.drain_available()

    assert [result.state for result in results] == [
        DispatchCycleState.PROCESSED,
        DispatchCycleState.PROCESSED,
        DispatchCycleState.IDLE,
    ]
    assert [event_type for event_type, _dispatch, _receipt in handler.calls] == [
        "audio.capture.degraded",
        "audio.capture.recovered",
    ]
    stats = queue.stats()
    assert stats.pending == 0
    assert stats.claimed == 0
    assert stats.processed == 2
