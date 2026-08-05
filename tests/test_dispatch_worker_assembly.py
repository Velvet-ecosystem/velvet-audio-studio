from __future__ import annotations

from pathlib import Path

from velvet_audio_studio.runtime.acknowledgement_store import (
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.court_routing import (
    CourtDecision,
    CourtDisposition,
)
from velvet_audio_studio.runtime.dispatch_worker import DispatchWorkerCycleState
from velvet_audio_studio.runtime.dispatch_worker_assembly import (
    build_runtime_dispatch_worker,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    event_protocol_idempotency_key,
)


class ApprovingCourt:
    def __init__(self) -> None:
        self.dispatch_ids: list[str] = []

    def decide(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> CourtDecision:
        del envelope, ingress_receipt_id
        self.dispatch_ids.append(dispatch_id)
        return CourtDecision(
            disposition=CourtDisposition.APPROVED,
            court_receipt_id="court-receipt-1",
            capability="audio.voice.route",
        )


class ReceiptRouter:
    def __init__(self) -> None:
        self.dispatch_ids: list[str] = []

    def route(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
        court_receipt_id: str,
        capability: str,
    ) -> str:
        del envelope, ingress_receipt_id, court_receipt_id, capability
        self.dispatch_ids.append(dispatch_id)
        return "organ-receipt-1"


def test_assembly_uses_ingress_database_and_routes_through_court(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    envelope = EventProtocolEnvelope(
        event_type="audio.voice_input.ready",
        source_id="octo.capture.primary",
        sequence=1,
        occurred_at_monotonic_ns=1_000,
        payload={"selected_logical_name": "driver_upper_mic"},
    )
    key = event_protocol_idempotency_key(envelope)
    SqliteAcknowledgementStore(database, clock_ns=lambda: 1_000).acknowledge(
        key,
        envelope,
    )
    court = ApprovingCourt()
    router = ReceiptRouter()
    assembly = build_runtime_dispatch_worker(
        database,
        court,
        router,
        worker_id="runtime-worker-1",
        wall_clock_ns=lambda: 2_000,
    )

    result = assembly.worker.run_cycle()

    assert assembly.database_path == database.resolve()
    assert result.state is DispatchWorkerCycleState.PROCESSED
    assert result.claim is not None
    assert court.dispatch_ids == [result.claim.dispatch_id]
    assert router.dispatch_ids == [result.claim.dispatch_id]
    assert result.downstream_receipt_id == "organ-receipt-1"
