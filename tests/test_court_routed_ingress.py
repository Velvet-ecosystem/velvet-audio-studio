from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from velvet_audio_studio.runtime.acknowledgement_store import (
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.court_routing import (
    CourtDecision,
    CourtDisposition,
    CourtRoutedIngressHandler,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    event_protocol_idempotency_key,
)
from velvet_audio_studio.runtime.ingress_dispatch import (
    DispatchCycleState,
    DurableIngressDispatcher,
    IngressDispatchStatus,
    SqliteIngressDispatchQueue,
)


@dataclass
class RecordingCourt:
    decision: CourtDecision | None = None
    failure: Exception | None = None

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def decide(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> CourtDecision:
        self.calls.append((envelope.event_type, dispatch_id, ingress_receipt_id))
        if self.failure is not None:
            raise self.failure
        assert self.decision is not None
        return self.decision


class RecordingRouter:
    def __init__(self, receipt: str = "route-receipt-1") -> None:
        self.receipt = receipt
        self.calls: list[dict[str, str]] = []

    def route(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
        court_receipt_id: str,
        capability: str,
    ) -> str:
        self.calls.append(
            {
                "event_type": envelope.event_type,
                "dispatch_id": dispatch_id,
                "ingress_receipt_id": ingress_receipt_id,
                "court_receipt_id": court_receipt_id,
                "capability": capability,
            }
        )
        return self.receipt


def _envelope() -> EventProtocolEnvelope:
    return EventProtocolEnvelope(
        event_type="audio.voice_input.ready",
        source_id="octo.capture.primary",
        sequence=4,
        occurred_at_monotonic_ns=4_000_000,
        payload={"selected_logical_name": "driver_upper_mic"},
    )


def _accepted_queue(tmp_path: Path) -> tuple[SqliteIngressDispatchQueue, str]:
    database = tmp_path / "runtime.sqlite3"
    envelope = _envelope()
    key = event_protocol_idempotency_key(envelope)
    store = SqliteAcknowledgementStore(database, clock_ns=lambda: 1_000)
    store.acknowledge(key, envelope)
    return SqliteIngressDispatchQueue(database, clock_ns=lambda: 2_000), key


def test_approved_event_carries_court_capability_into_router() -> None:
    court = RecordingCourt(
        CourtDecision(
            disposition=CourtDisposition.APPROVED,
            court_receipt_id="court-receipt-1",
            capability="audio.voice.route",
            reason="voice input is permitted",
        )
    )
    router = RecordingRouter("organ-receipt-1")
    handler = CourtRoutedIngressHandler(court, router)

    receipt = handler.dispatch(
        _envelope(),
        dispatch_id="runtime-dispatch-1",
        ingress_receipt_id="runtime-receipt-1",
    )

    assert receipt == "organ-receipt-1"
    assert router.calls == [
        {
            "event_type": "audio.voice_input.ready",
            "dispatch_id": "runtime-dispatch-1",
            "ingress_receipt_id": "runtime-receipt-1",
            "court_receipt_id": "court-receipt-1",
            "capability": "audio.voice.route",
        }
    ]


def test_durable_court_denial_finishes_without_calling_router() -> None:
    court = RecordingCourt(
        CourtDecision(
            disposition=CourtDisposition.DENIED,
            court_receipt_id="court-denial-receipt-1",
            reason="voice routing is not authorized",
        )
    )
    router = RecordingRouter()
    handler = CourtRoutedIngressHandler(court, router)

    receipt = handler.dispatch(
        _envelope(),
        dispatch_id="runtime-dispatch-1",
        ingress_receipt_id="runtime-receipt-1",
    )

    assert receipt == "court-denial-receipt-1"
    assert router.calls == []


def test_invalid_court_decisions_fail_closed() -> None:
    with pytest.raises(ValueError, match="capability"):
        CourtDecision(
            disposition=CourtDisposition.APPROVED,
            court_receipt_id="court-receipt-1",
        )

    with pytest.raises(ValueError, match="denial reason"):
        CourtDecision(
            disposition=CourtDisposition.DENIED,
            court_receipt_id="court-receipt-1",
        )

    with pytest.raises(ValueError, match="cannot grant"):
        CourtDecision(
            disposition=CourtDisposition.DENIED,
            court_receipt_id="court-receipt-1",
            capability="audio.route",
            reason="denied",
        )


def test_empty_router_receipt_is_not_completion() -> None:
    court = RecordingCourt(
        CourtDecision(
            disposition=CourtDisposition.APPROVED,
            court_receipt_id="court-receipt-1",
            capability="audio.voice.route",
        )
    )
    handler = CourtRoutedIngressHandler(court, RecordingRouter(""))

    with pytest.raises(ValueError, match="route receipt"):
        handler.dispatch(
            _envelope(),
            dispatch_id="runtime-dispatch-1",
            ingress_receipt_id="runtime-receipt-1",
        )


def test_court_failure_returns_event_to_pending_for_retry(tmp_path: Path) -> None:
    queue, key = _accepted_queue(tmp_path)
    court = RecordingCourt(failure=RuntimeError("Court ledger unavailable"))
    router = RecordingRouter()
    dispatcher = DurableIngressDispatcher(
        queue,
        CourtRoutedIngressHandler(court, router),
        worker_id="runtime-court-worker",
        lease_seconds=5,
    )

    result = dispatcher.process_one()
    record = queue.get(key)

    assert result.state is DispatchCycleState.RETRY
    assert record is not None
    assert record.status is IngressDispatchStatus.PENDING
    assert "Court ledger unavailable" in (record.last_error or "")
    assert router.calls == []


def test_court_denial_is_committed_as_processed_receipt(tmp_path: Path) -> None:
    queue, key = _accepted_queue(tmp_path)
    court = RecordingCourt(
        CourtDecision(
            disposition=CourtDisposition.DENIED,
            court_receipt_id="court-denial-receipt-9",
            reason="capability denied",
        )
    )
    dispatcher = DurableIngressDispatcher(
        queue,
        CourtRoutedIngressHandler(court, RecordingRouter()),
        worker_id="runtime-court-worker",
        lease_seconds=5,
    )

    result = dispatcher.process_one()
    record = queue.get(key)

    assert result.state is DispatchCycleState.PROCESSED
    assert result.downstream_receipt_id == "court-denial-receipt-9"
    assert record is not None
    assert record.status is IngressDispatchStatus.PROCESSED
    assert record.downstream_receipt_id == "court-denial-receipt-9"
