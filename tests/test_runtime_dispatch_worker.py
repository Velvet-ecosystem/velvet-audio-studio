from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
import time

from velvet_audio_studio.runtime.acknowledgement_store import (
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.dispatch_quarantine import (
    QuarantinableIngressDispatchQueue,
)
from velvet_audio_studio.runtime.dispatch_worker import (
    DispatchBackoffPolicy,
    DispatchWorkerCycleState,
    InMemoryDispatchWorkerEventSink,
    PermanentDispatchError,
    RuntimeDispatchWorker,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    event_protocol_idempotency_key,
)


class MutableWallClock:
    def __init__(self, value: int = 1_000_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class AdvancingMonotonicClock:
    def __init__(self) -> None:
        self.value = 0
        self.sleeps: list[float] = []

    def __call__(self) -> int:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += int(seconds * 1_000_000_000)


class ReceiptHandler:
    def __init__(self, receipt: str = "organ-receipt-1") -> None:
        self.receipt = receipt
        self.calls: list[str] = []

    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        del envelope, ingress_receipt_id
        self.calls.append(dispatch_id)
        return self.receipt


class TransientFailureHandler:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        del envelope, dispatch_id, ingress_receipt_id
        self.calls += 1
        raise RuntimeError("Court temporarily unavailable")


class PoisonThenReceiptHandler:
    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        del dispatch_id, ingress_receipt_id
        if envelope.sequence == 1:
            raise PermanentDispatchError("unsupported fixed event schema")
        return "organ-receipt-second"


class BlockingHandler:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        del envelope, dispatch_id, ingress_receipt_id
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("test handler timed out")
        return "slow-organ-receipt"


class BrokenHealthSink:
    def emit(self, event: object) -> None:
        del event
        raise RuntimeError("telemetry sink unavailable")


def _envelope(sequence: int) -> EventProtocolEnvelope:
    return EventProtocolEnvelope(
        event_type="audio.voice_input.ready",
        source_id="octo.capture.primary",
        sequence=sequence,
        occurred_at_monotonic_ns=sequence * 1_000,
        payload={"selected_logical_name": "driver_upper_mic"},
    )


def _queue_with_events(
    tmp_path: Path,
    *sequences: int,
    real_clock: bool = False,
) -> QuarantinableIngressDispatchQueue:
    database = tmp_path / "runtime.sqlite3"
    if real_clock:
        store = SqliteAcknowledgementStore(database)
        for sequence in sequences:
            envelope = _envelope(sequence)
            store.acknowledge(event_protocol_idempotency_key(envelope), envelope)
        return QuarantinableIngressDispatchQueue(database)

    clock = MutableWallClock()
    store = SqliteAcknowledgementStore(database, clock_ns=clock)
    for sequence in sequences:
        envelope = _envelope(sequence)
        store.acknowledge(event_protocol_idempotency_key(envelope), envelope)
        clock.value += 1
    return QuarantinableIngressDispatchQueue(database, clock_ns=clock)


def test_worker_processes_one_event_and_emits_receipt_evidence(tmp_path: Path) -> None:
    queue = _queue_with_events(tmp_path, 1)
    sink = InMemoryDispatchWorkerEventSink()
    handler = ReceiptHandler()
    worker = RuntimeDispatchWorker(
        queue,
        handler,
        worker_id="runtime-worker-1",
        event_sink=sink,
    )

    result = worker.run_cycle()

    assert result.state is DispatchWorkerCycleState.PROCESSED
    assert result.downstream_receipt_id == "organ-receipt-1"
    assert worker.status.processed == 1
    assert len(handler.calls) == 1
    assert [event.event for event in sink.events] == [
        "runtime.dispatch.claimed",
        "runtime.dispatch.processed",
    ]


def test_transient_failures_retry_without_quarantine(tmp_path: Path) -> None:
    queue = _queue_with_events(tmp_path, 1)
    worker = RuntimeDispatchWorker(
        queue,
        TransientFailureHandler(),
        worker_id="runtime-worker-1",
        quarantine_after_failures=2,
    )

    first = worker.run_cycle()
    second = worker.run_cycle()
    third = worker.run_cycle()

    assert [first.state, second.state, third.state] == [
        DispatchWorkerCycleState.RETRY,
        DispatchWorkerCycleState.RETRY,
        DispatchWorkerCycleState.RETRY,
    ]
    assert queue.quarantined_count() == 0
    assert first.failure_evidence is not None
    assert third.failure_evidence is not None
    assert third.failure_evidence.consecutive_count == 3
    assert third.failure_evidence.poison_reason is None


def test_explicit_poison_is_quarantined_then_next_event_advances(tmp_path: Path) -> None:
    queue = _queue_with_events(tmp_path, 1, 2)
    sink = InMemoryDispatchWorkerEventSink()
    worker = RuntimeDispatchWorker(
        queue,
        PoisonThenReceiptHandler(),
        worker_id="runtime-worker-1",
        quarantine_after_failures=3,
        event_sink=sink,
    )

    first = worker.run_cycle()
    second = worker.run_cycle()
    third = worker.run_cycle()
    fourth = worker.run_cycle()

    assert first.state is DispatchWorkerCycleState.RETRY
    assert second.state is DispatchWorkerCycleState.RETRY
    assert third.state is DispatchWorkerCycleState.QUARANTINED
    assert third.downstream_receipt_id is not None
    assert third.downstream_receipt_id.startswith("runtime-quarantine-")
    assert fourth.state is DispatchWorkerCycleState.PROCESSED
    assert fourth.claim is not None
    assert fourth.claim.envelope.sequence == 2
    assert queue.quarantined_count() == 1
    assert "runtime.dispatch.quarantined" in [event.event for event in sink.events]


def test_run_uses_bounded_failure_backoff_and_stops_cleanly(tmp_path: Path) -> None:
    queue = _queue_with_events(tmp_path, 1)
    clock = AdvancingMonotonicClock()
    worker = RuntimeDispatchWorker(
        queue,
        TransientFailureHandler(),
        worker_id="runtime-worker-1",
        backoff_policy=DispatchBackoffPolicy(
            initial_seconds=0.25,
            multiplier=2.0,
            maximum_seconds=0.5,
        ),
        heartbeat_interval_seconds=10,
        sleep_quantum_seconds=1.0,
        clock_ns=clock,
        sleeper=clock.sleep,
    )

    result = worker.run(max_cycles=3)

    assert [cycle.state for cycle in result.cycles] == [
        DispatchWorkerCycleState.RETRY,
        DispatchWorkerCycleState.RETRY,
        DispatchWorkerCycleState.RETRY,
    ]
    assert clock.sleeps == [0.25, 0.5, 0.5]
    assert worker.status.state.value == "stopped"
    assert worker.status.consecutive_failures == 3


def test_health_sink_failure_never_owns_dispatch_authority(tmp_path: Path) -> None:
    queue = _queue_with_events(tmp_path, 1)
    worker = RuntimeDispatchWorker(
        queue,
        ReceiptHandler(),
        worker_id="runtime-worker-1",
        event_sink=BrokenHealthSink(),
    )

    result = worker.run(max_cycles=1)

    assert result.cycles[0].state is DispatchWorkerCycleState.PROCESSED
    assert worker.status.processed == 1
    assert worker.status.health_event_failures > 0


def test_slow_handler_receives_lease_heartbeats_until_completion(tmp_path: Path) -> None:
    queue = _queue_with_events(tmp_path, 1, real_clock=True)
    sink = InMemoryDispatchWorkerEventSink()
    handler = BlockingHandler()
    worker = RuntimeDispatchWorker(
        queue,
        handler,
        worker_id="runtime-worker-1",
        lease_seconds=0.20,
        lease_heartbeat_seconds=0.04,
        heartbeat_interval_seconds=0.04,
        event_sink=sink,
    )
    result_holder: list[object] = []

    thread = Thread(target=lambda: result_holder.append(worker.run_cycle()), daemon=True)
    thread.start()
    assert handler.started.wait(timeout=1.0)
    time.sleep(0.11)
    handler.release.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    result = result_holder[0]
    assert getattr(result, "state") is DispatchWorkerCycleState.PROCESSED
    event_names = [event.event for event in sink.events]
    assert "runtime.dispatch.lease_renewed" in event_names
    assert "runtime.dispatch.worker.heartbeat" in event_names


def test_stop_request_ends_idle_worker_without_claiming_future_work(tmp_path: Path) -> None:
    queue = _queue_with_events(tmp_path)
    sink = InMemoryDispatchWorkerEventSink()
    worker = RuntimeDispatchWorker(
        queue,
        ReceiptHandler(),
        worker_id="runtime-worker-1",
        idle_poll_seconds=0,
        event_sink=sink,
    )

    result = worker.run(stop_requested=lambda: True)

    assert result.cycles == ()
    assert result.stopped_by_request is True
    assert [event.event for event in sink.events] == [
        "runtime.dispatch.worker.started",
        "runtime.dispatch.worker.stopping",
        "runtime.dispatch.worker.stopped",
    ]
