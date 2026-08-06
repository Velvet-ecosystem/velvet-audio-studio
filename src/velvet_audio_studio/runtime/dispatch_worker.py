"""Long-running Runtime ingress worker with leases, backoff, and quarantine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from queue import Queue
from threading import Thread
from time import monotonic_ns, sleep
from typing import Callable, Protocol

from velvet_audio_studio.runtime.dispatch_quarantine import (
    DispatchFailureEvidence,
    QuarantinableIngressDispatchQueue,
    QuarantinedDispatch,
)
from velvet_audio_studio.runtime.ingress_dispatch import (
    IngressClaimLostError,
    IngressDispatchClaim,
    IngressDispatchError,
    RuntimeIngressHandler,
)


class PermanentDispatchError(RuntimeError):
    """Explicitly marks a deterministic event failure as poison-eligible."""


class PoisonEventClassifier(Protocol):
    def classify(
        self,
        claim: IngressDispatchClaim,
        error: BaseException,
    ) -> str | None:
        """Return a quarantine reason only for deterministic poison evidence."""
        ...


class ExplicitPermanentFailureClassifier:
    """Quarantine only failures explicitly raised as PermanentDispatchError."""

    def classify(
        self,
        claim: IngressDispatchClaim,
        error: BaseException,
    ) -> str | None:
        del claim
        if isinstance(error, PermanentDispatchError):
            return str(error).strip() or "explicit permanent dispatch failure"
        return None


@dataclass(frozen=True)
class DispatchBackoffPolicy:
    initial_seconds: float = 0.25
    multiplier: float = 2.0
    maximum_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.initial_seconds <= 0:
            raise ValueError("initial_seconds must be positive")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.maximum_seconds < self.initial_seconds:
            raise ValueError("maximum_seconds cannot be below initial_seconds")

    def delay_for(self, consecutive_failures: int) -> float:
        if consecutive_failures <= 0:
            return 0.0
        delay = self.initial_seconds * self.multiplier ** (consecutive_failures - 1)
        return min(self.maximum_seconds, delay)


class DispatchWorkerState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    BACKING_OFF = "backing_off"
    STOPPING = "stopping"


class DispatchWorkerCycleState(StrEnum):
    IDLE = "idle"
    PROCESSED = "processed"
    RETRY = "retry"
    QUARANTINED = "quarantined"
    CLAIM_LOST = "claim_lost"
    ERROR = "error"


@dataclass(frozen=True)
class DispatchWorkerEvent:
    event: str
    worker_id: str
    occurred_at_monotonic_ns: int
    payload: dict[str, object]


class DispatchWorkerEventSink(Protocol):
    def emit(self, event: DispatchWorkerEvent) -> None:
        ...


class NullDispatchWorkerEventSink:
    def emit(self, event: DispatchWorkerEvent) -> None:
        del event


class InMemoryDispatchWorkerEventSink:
    def __init__(self) -> None:
        self.events: list[DispatchWorkerEvent] = []

    def emit(self, event: DispatchWorkerEvent) -> None:
        self.events.append(event)


@dataclass(frozen=True)
class DispatchWorkerCycleResult:
    state: DispatchWorkerCycleState
    claim: IngressDispatchClaim | None = None
    downstream_receipt_id: str | None = None
    quarantine: QuarantinedDispatch | None = None
    failure_evidence: DispatchFailureEvidence | None = None
    error: str | None = None


@dataclass(frozen=True)
class DispatchWorkerStatus:
    state: DispatchWorkerState
    cycles: int
    processed: int
    retries: int
    quarantined: int
    claim_losses: int
    infrastructure_errors: int
    consecutive_failures: int
    health_event_failures: int
    active_dispatch_id: str | None
    last_heartbeat_monotonic_ns: int | None


@dataclass(frozen=True)
class DispatchWorkerRunResult:
    cycles: tuple[DispatchWorkerCycleResult, ...]
    stopped_by_request: bool


@dataclass(frozen=True)
class _HandlerOutcome:
    receipt_id: str | None = None
    error: BaseException | None = None


class RuntimeDispatchWorker:
    """Continuously claims ordered ingress events and dispatches them through Court."""

    def __init__(
        self,
        queue: QuarantinableIngressDispatchQueue,
        handler: RuntimeIngressHandler,
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
        lease_heartbeat_seconds: float = 10.0,
        heartbeat_interval_seconds: float = 5.0,
        idle_poll_seconds: float = 0.1,
        backoff_policy: DispatchBackoffPolicy = DispatchBackoffPolicy(),
        poison_classifier: PoisonEventClassifier = ExplicitPermanentFailureClassifier(),
        quarantine_after_failures: int = 3,
        event_sink: DispatchWorkerEventSink | None = None,
        clock_ns: Callable[[], int] = monotonic_ns,
        sleeper: Callable[[float], None] = sleep,
        sleep_quantum_seconds: float = 0.1,
    ) -> None:
        self.queue = queue
        self.handler = handler
        self.worker_id = _nonempty(worker_id, "worker_id")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if lease_heartbeat_seconds <= 0:
            raise ValueError("lease_heartbeat_seconds must be positive")
        if lease_heartbeat_seconds >= lease_seconds:
            raise ValueError("lease heartbeat must be shorter than the lease")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if idle_poll_seconds < 0:
            raise ValueError("idle_poll_seconds cannot be negative")
        if quarantine_after_failures <= 0:
            raise ValueError("quarantine_after_failures must be positive")
        if sleep_quantum_seconds <= 0:
            raise ValueError("sleep_quantum_seconds must be positive")

        self.lease_seconds = float(lease_seconds)
        self.lease_heartbeat_seconds = float(lease_heartbeat_seconds)
        self.heartbeat_interval_ns = int(heartbeat_interval_seconds * 1_000_000_000)
        self.idle_poll_seconds = float(idle_poll_seconds)
        self.backoff_policy = backoff_policy
        self.poison_classifier = poison_classifier
        self.quarantine_after_failures = quarantine_after_failures
        self.event_sink = event_sink or NullDispatchWorkerEventSink()
        self.clock_ns = clock_ns
        self.sleeper = sleeper
        self.sleep_quantum_seconds = float(sleep_quantum_seconds)

        self.state = DispatchWorkerState.STOPPED
        self.cycles = 0
        self.processed = 0
        self.retries = 0
        self.quarantined = 0
        self.claim_losses = 0
        self.infrastructure_errors = 0
        self.consecutive_failures = 0
        self.health_event_failures = 0
        self.active_dispatch_id: str | None = None
        self.last_heartbeat_monotonic_ns: int | None = None

    @property
    def status(self) -> DispatchWorkerStatus:
        return DispatchWorkerStatus(
            state=self.state,
            cycles=self.cycles,
            processed=self.processed,
            retries=self.retries,
            quarantined=self.quarantined,
            claim_losses=self.claim_losses,
            infrastructure_errors=self.infrastructure_errors,
            consecutive_failures=self.consecutive_failures,
            health_event_failures=self.health_event_failures,
            active_dispatch_id=self.active_dispatch_id,
            last_heartbeat_monotonic_ns=self.last_heartbeat_monotonic_ns,
        )

    def run_cycle(self) -> DispatchWorkerCycleResult:
        try:
            claim = self.queue.claim_next(
                self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except Exception as exc:
            self.infrastructure_errors += 1
            self.consecutive_failures += 1
            error = _error_text(exc)
            self._emit("runtime.dispatch.worker.error", {"phase": "claim", "error": error})
            return DispatchWorkerCycleResult(
                state=DispatchWorkerCycleState.ERROR,
                error=error,
            )

        if claim is None:
            return DispatchWorkerCycleResult(state=DispatchWorkerCycleState.IDLE)

        self.active_dispatch_id = claim.dispatch_id
        self._emit(
            "runtime.dispatch.claimed",
            {
                "dispatch_id": claim.dispatch_id,
                "ingress_receipt_id": claim.ingress_receipt_id,
                "event_type": claim.envelope.event_type,
                "attempt_count": claim.attempt_count,
                "lease_expires_at_unix_ns": claim.lease_expires_at_unix_ns,
            },
        )
        outcome, claim_lost = self._run_handler_with_lease(claim)
        self.active_dispatch_id = None

        if claim_lost is not None:
            self.claim_losses += 1
            self.consecutive_failures += 1
            self._emit(
                "runtime.dispatch.claim_lost",
                {
                    "dispatch_id": claim.dispatch_id,
                    "error": claim_lost,
                    "downstream_receipt_id": outcome.receipt_id,
                },
            )
            return DispatchWorkerCycleResult(
                state=DispatchWorkerCycleState.CLAIM_LOST,
                claim=claim,
                downstream_receipt_id=outcome.receipt_id,
                error=claim_lost,
            )

        if outcome.error is not None:
            return self._handle_dispatch_failure(claim, outcome.error)

        assert outcome.receipt_id is not None
        try:
            self.queue.complete(claim.claim_token, outcome.receipt_id)
        except IngressClaimLostError as exc:
            self.claim_losses += 1
            self.consecutive_failures += 1
            error = _error_text(exc)
            self._emit(
                "runtime.dispatch.claim_lost",
                {
                    "dispatch_id": claim.dispatch_id,
                    "error": error,
                    "downstream_receipt_id": outcome.receipt_id,
                },
            )
            return DispatchWorkerCycleResult(
                state=DispatchWorkerCycleState.CLAIM_LOST,
                claim=claim,
                downstream_receipt_id=outcome.receipt_id,
                error=error,
            )
        except Exception as exc:
            self.infrastructure_errors += 1
            self.consecutive_failures += 1
            error = _error_text(exc)
            self._emit(
                "runtime.dispatch.worker.error",
                {
                    "phase": "complete",
                    "dispatch_id": claim.dispatch_id,
                    "error": error,
                    "downstream_receipt_id": outcome.receipt_id,
                },
            )
            return DispatchWorkerCycleResult(
                state=DispatchWorkerCycleState.ERROR,
                claim=claim,
                downstream_receipt_id=outcome.receipt_id,
                error=error,
            )

        self.processed += 1
        self.consecutive_failures = 0
        self._emit(
            "runtime.dispatch.processed",
            {
                "dispatch_id": claim.dispatch_id,
                "ingress_receipt_id": claim.ingress_receipt_id,
                "downstream_receipt_id": outcome.receipt_id,
                "attempt_count": claim.attempt_count,
            },
        )
        return DispatchWorkerCycleResult(
            state=DispatchWorkerCycleState.PROCESSED,
            claim=claim,
            downstream_receipt_id=outcome.receipt_id,
        )

    def run(
        self,
        *,
        stop_requested: Callable[[], bool] = lambda: False,
        max_cycles: int | None = None,
    ) -> DispatchWorkerRunResult:
        if self.state is not DispatchWorkerState.STOPPED:
            raise RuntimeError("dispatch worker is already running")
        if max_cycles is not None and max_cycles < 0:
            raise ValueError("max_cycles must be non-negative")

        self.state = DispatchWorkerState.RUNNING
        stopped_by_request = False
        results: list[DispatchWorkerCycleResult] = []
        self._emit("runtime.dispatch.worker.started", self._health_payload())
        try:
            while max_cycles is None or len(results) < max_cycles:
                if stop_requested():
                    stopped_by_request = True
                    break
                result = self.run_cycle()
                self.cycles += 1
                results.append(result)
                self._maybe_heartbeat()

                delay = self._delay_after(result)
                if delay > 0:
                    self.state = (
                        DispatchWorkerState.BACKING_OFF
                        if result.state
                        in {
                            DispatchWorkerCycleState.RETRY,
                            DispatchWorkerCycleState.CLAIM_LOST,
                            DispatchWorkerCycleState.ERROR,
                        }
                        else DispatchWorkerState.RUNNING
                    )
                    if self._sleep_interruptibly(delay, stop_requested):
                        stopped_by_request = True
                        break
                    self.state = DispatchWorkerState.RUNNING
        finally:
            self.state = DispatchWorkerState.STOPPING
            self._emit("runtime.dispatch.worker.stopping", self._health_payload())
            self.state = DispatchWorkerState.STOPPED
            self._emit("runtime.dispatch.worker.stopped", self._health_payload())
        return DispatchWorkerRunResult(
            cycles=tuple(results),
            stopped_by_request=stopped_by_request,
        )

    def emit_heartbeat(self) -> None:
        now_ns = self._clock()
        self.last_heartbeat_monotonic_ns = now_ns
        self._emit(
            "runtime.dispatch.worker.heartbeat",
            self._health_payload(),
            occurred_at_monotonic_ns=now_ns,
        )

    def _run_handler_with_lease(
        self,
        claim: IngressDispatchClaim,
    ) -> tuple[_HandlerOutcome, str | None]:
        outcomes: Queue[_HandlerOutcome] = Queue(maxsize=1)

        def invoke() -> None:
            try:
                receipt = self.handler.dispatch(
                    claim.envelope,
                    dispatch_id=claim.dispatch_id,
                    ingress_receipt_id=claim.ingress_receipt_id,
                )
                outcomes.put(_HandlerOutcome(receipt_id=_nonempty(receipt, "downstream receipt")))
            except BaseException as exc:
                outcomes.put(_HandlerOutcome(error=exc))

        thread = Thread(
            target=invoke,
            name=f"velvet-dispatch-{claim.dispatch_id[-8:]}",
            daemon=True,
        )
        thread.start()
        claim_lost: str | None = None
        while thread.is_alive():
            thread.join(timeout=self.lease_heartbeat_seconds)
            self._maybe_heartbeat()
            if not thread.is_alive() or claim_lost is not None:
                continue
            try:
                new_expiry = self.queue.renew(
                    claim.claim_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception as exc:
                claim_lost = _error_text(exc)
                continue
            self._emit(
                "runtime.dispatch.lease_renewed",
                {
                    "dispatch_id": claim.dispatch_id,
                    "lease_expires_at_unix_ns": new_expiry,
                },
            )
        thread.join()
        return outcomes.get(), claim_lost

    def _handle_dispatch_failure(
        self,
        claim: IngressDispatchClaim,
        error: BaseException,
    ) -> DispatchWorkerCycleResult:
        error_text = _error_text(error)
        poison_reason = self.poison_classifier.classify(claim, error)
        try:
            self.queue.release(claim.claim_token, error_text)
            evidence = self.queue.record_failure(
                claim.idempotency_key,
                error_text,
                poison_reason=poison_reason,
            )
            quarantine = None
            if poison_reason is not None:
                quarantine = self.queue.quarantine_if_threshold(
                    claim.idempotency_key,
                    failure_fingerprint=evidence.failure_fingerprint,
                    reason=poison_reason,
                    minimum_consecutive_failures=self.quarantine_after_failures,
                )
        except IngressClaimLostError as exc:
            self.claim_losses += 1
            self.consecutive_failures += 1
            lost = _error_text(exc)
            self._emit(
                "runtime.dispatch.claim_lost",
                {"dispatch_id": claim.dispatch_id, "error": lost},
            )
            return DispatchWorkerCycleResult(
                state=DispatchWorkerCycleState.CLAIM_LOST,
                claim=claim,
                error=f"{error_text}; {lost}",
            )
        except Exception as exc:
            self.infrastructure_errors += 1
            self.consecutive_failures += 1
            storage_error = _error_text(exc)
            self._emit(
                "runtime.dispatch.worker.error",
                {
                    "phase": "failure_evidence",
                    "dispatch_id": claim.dispatch_id,
                    "error": storage_error,
                    "handler_error": error_text,
                },
            )
            return DispatchWorkerCycleResult(
                state=DispatchWorkerCycleState.ERROR,
                claim=claim,
                error=f"{error_text}; {storage_error}",
            )

        if quarantine is not None:
            self.quarantined += 1
            self.consecutive_failures = 0
            self._emit(
                "runtime.dispatch.quarantined",
                {
                    "dispatch_id": claim.dispatch_id,
                    "quarantine_receipt_id": quarantine.quarantine_receipt_id,
                    "reason": quarantine.reason,
                    "consecutive_failures": quarantine.consecutive_failures,
                    "failure_fingerprint": quarantine.failure_fingerprint,
                },
            )
            return DispatchWorkerCycleResult(
                state=DispatchWorkerCycleState.QUARANTINED,
                claim=claim,
                downstream_receipt_id=quarantine.quarantine_receipt_id,
                quarantine=quarantine,
                failure_evidence=evidence,
                error=error_text,
            )

        self.retries += 1
        self.consecutive_failures += 1
        self._emit(
            "runtime.dispatch.retry",
            {
                "dispatch_id": claim.dispatch_id,
                "error": error_text,
                "failure_fingerprint": evidence.failure_fingerprint,
                "consecutive_same_failure": evidence.consecutive_count,
                "poison_eligible": poison_reason is not None,
            },
        )
        return DispatchWorkerCycleResult(
            state=DispatchWorkerCycleState.RETRY,
            claim=claim,
            failure_evidence=evidence,
            error=error_text,
        )

    def _delay_after(self, result: DispatchWorkerCycleResult) -> float:
        if result.state is DispatchWorkerCycleState.IDLE:
            return self.idle_poll_seconds
        if result.state in {
            DispatchWorkerCycleState.RETRY,
            DispatchWorkerCycleState.CLAIM_LOST,
            DispatchWorkerCycleState.ERROR,
        }:
            return self.backoff_policy.delay_for(self.consecutive_failures)
        return 0.0

    def _sleep_interruptibly(
        self,
        delay_seconds: float,
        stop_requested: Callable[[], bool],
    ) -> bool:
        remaining = delay_seconds
        while remaining > 0:
            if stop_requested():
                return True
            step = min(self.sleep_quantum_seconds, remaining)
            self.sleeper(step)
            remaining -= step
            self._maybe_heartbeat()
        return stop_requested()

    def _maybe_heartbeat(self) -> None:
        now_ns = self._clock()
        previous = self.last_heartbeat_monotonic_ns
        if previous is None or now_ns - previous >= self.heartbeat_interval_ns:
            self.emit_heartbeat()

    def _health_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "state": self.state.value,
            "cycles": self.cycles,
            "processed": self.processed,
            "retries": self.retries,
            "quarantined": self.quarantined,
            "claim_losses": self.claim_losses,
            "infrastructure_errors": self.infrastructure_errors,
            "consecutive_failures": self.consecutive_failures,
            "active_dispatch_id": self.active_dispatch_id,
        }
        try:
            stats = self.queue.stats()
            payload.update(
                {
                    "queue_pending": stats.pending,
                    "queue_claimed": stats.claimed,
                    "queue_processed": stats.processed,
                    "queue_expired_claims": stats.expired_claims,
                    "queue_quarantined": self.queue.quarantined_count(),
                }
            )
        except Exception as exc:
            payload["queue_health_error"] = _error_text(exc)
        return payload

    def _emit(
        self,
        event_name: str,
        payload: dict[str, object],
        *,
        occurred_at_monotonic_ns: int | None = None,
    ) -> None:
        event = DispatchWorkerEvent(
            event=event_name,
            worker_id=self.worker_id,
            occurred_at_monotonic_ns=(
                self._clock()
                if occurred_at_monotonic_ns is None
                else occurred_at_monotonic_ns
            ),
            payload=dict(payload),
        )
        try:
            self.event_sink.emit(event)
        except Exception:
            self.health_event_failures += 1

    def _clock(self) -> int:
        value = self.clock_ns()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("worker clock must return a non-negative integer")
        return value


def _error_text(error: BaseException) -> str:
    message = str(error).strip()
    if message:
        return f"{type(error).__name__}: {message}"[:4_096]
    return type(error).__name__


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()
