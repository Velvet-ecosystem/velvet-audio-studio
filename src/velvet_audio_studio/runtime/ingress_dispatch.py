"""Durable claim-and-dispatch layer for acknowledged Runtime ingress events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
import sqlite3
from time import time_ns
from typing import Callable, Protocol

from velvet_audio_studio.runtime.acknowledgement_store import (
    AcknowledgementStoreError,
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.event_protocol import EventProtocolEnvelope
from velvet_audio_studio.runtime.http_receiver import parse_event_protocol_envelope


class IngressDispatchError(RuntimeError):
    """Raised when the durable dispatch queue cannot be used safely."""


class IngressClaimLostError(IngressDispatchError):
    """Raised when a worker no longer owns the claim it is trying to mutate."""


class IngressDispatchStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PROCESSED = "processed"


@dataclass(frozen=True)
class IngressDispatchClaim:
    idempotency_key: str
    dispatch_id: str
    ingress_receipt_id: str
    claim_token: str
    claimed_by: str
    claimed_at_unix_ns: int
    lease_expires_at_unix_ns: int
    accepted_at_unix_ns: int
    attempt_count: int
    envelope: EventProtocolEnvelope


@dataclass(frozen=True)
class IngressDispatchRecord:
    idempotency_key: str
    dispatch_id: str
    ingress_receipt_id: str
    status: IngressDispatchStatus
    claimed_by: str | None
    claim_token: str | None
    claimed_at_unix_ns: int | None
    lease_expires_at_unix_ns: int | None
    attempt_count: int
    last_error: str | None
    processed_at_unix_ns: int | None
    downstream_receipt_id: str | None


@dataclass(frozen=True)
class IngressDispatchStats:
    pending: int
    claimed: int
    processed: int
    expired_claims: int


class SqliteIngressDispatchQueue:
    """Atomically leases accepted ingress events to one Runtime worker at a time."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock_ns: Callable[[], int] = time_ns,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("dispatch queue timeout must be positive")
        self.path = Path(path).expanduser().resolve()
        self.clock_ns = clock_ns
        self.timeout_seconds = float(timeout_seconds)
        try:
            SqliteAcknowledgementStore(
                self.path,
                timeout_seconds=self.timeout_seconds,
            )
        except AcknowledgementStoreError as exc:
            raise IngressDispatchError(str(exc)) from exc

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
    ) -> IngressDispatchClaim | None:
        worker = _nonempty(worker_id, "worker_id")
        lease_ns = _lease_nanoseconds(lease_seconds)
        observed_ns = self._clock()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    state.idempotency_key,
                    state.dispatch_id,
                    acknowledgements.receipt_id,
                    acknowledgements.canonical_envelope,
                    acknowledgements.accepted_at_unix_ns,
                    state.attempt_count
                FROM event_dispatch_state AS state
                JOIN event_acknowledgements AS acknowledgements
                  ON acknowledgements.idempotency_key = state.idempotency_key
                WHERE state.status != 'processed'
                  AND (
                        state.claim_token IS NULL
                        OR state.lease_expires_at_unix_ns <= ?
                      )
                ORDER BY
                    acknowledgements.accepted_at_unix_ns ASC,
                    state.idempotency_key ASC
                LIMIT 1
                """,
                (observed_ns,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            try:
                envelope = parse_event_protocol_envelope(bytes(row[3]))
            except (TypeError, ValueError) as exc:
                connection.rollback()
                raise IngressDispatchError(
                    f"stored ingress envelope is invalid for {row[0]}: {exc}"
                ) from exc

            attempt_count = int(row[5]) + 1
            expires_ns = observed_ns + lease_ns
            claim_token = _claim_token(
                dispatch_id=row[1],
                worker_id=worker,
                claimed_at_unix_ns=observed_ns,
                attempt_count=attempt_count,
            )
            connection.execute(
                """
                UPDATE event_dispatch_state
                SET status = 'claimed',
                    claimed_by = ?,
                    claim_token = ?,
                    claimed_at_unix_ns = ?,
                    lease_expires_at_unix_ns = ?,
                    attempt_count = ?
                WHERE idempotency_key = ?
                """,
                (
                    worker,
                    claim_token,
                    observed_ns,
                    expires_ns,
                    attempt_count,
                    row[0],
                ),
            )
            connection.commit()
            return IngressDispatchClaim(
                idempotency_key=row[0],
                dispatch_id=row[1],
                ingress_receipt_id=row[2],
                claim_token=claim_token,
                claimed_by=worker,
                claimed_at_unix_ns=observed_ns,
                lease_expires_at_unix_ns=expires_ns,
                accepted_at_unix_ns=row[4],
                attempt_count=attempt_count,
                envelope=envelope,
            )
        except IngressDispatchError:
            raise
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise IngressDispatchError(f"dispatch claim transaction failed: {exc}") from exc
        finally:
            connection.close()

    def renew(self, claim_token: str, *, lease_seconds: float = 30.0) -> int:
        token = _nonempty(claim_token, "claim_token")
        lease_ns = _lease_nanoseconds(lease_seconds)
        observed_ns = self._clock()
        new_expiry_ns = observed_ns + lease_ns
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, claim_token, lease_expires_at_unix_ns
                FROM event_dispatch_state
                WHERE claim_token = ?
                """,
                (token,),
            ).fetchone()
            if (
                row is None
                or row[0] != IngressDispatchStatus.CLAIMED.value
                or row[1] != token
                or row[2] is None
                or row[2] <= observed_ns
            ):
                connection.rollback()
                raise IngressClaimLostError("dispatch claim cannot be renewed")
            connection.execute(
                """
                UPDATE event_dispatch_state
                SET lease_expires_at_unix_ns = ?
                WHERE claim_token = ?
                """,
                (new_expiry_ns, token),
            )
            connection.commit()
            return new_expiry_ns
        except IngressClaimLostError:
            raise
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise IngressDispatchError(f"dispatch lease renewal failed: {exc}") from exc
        finally:
            connection.close()

    def complete(
        self,
        claim_token: str,
        downstream_receipt_id: str,
    ) -> IngressDispatchRecord:
        token = _nonempty(claim_token, "claim_token")
        downstream_receipt = _nonempty(
            downstream_receipt_id,
            "downstream_receipt_id",
        )
        observed_ns = self._clock()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT idempotency_key, status, claim_token, lease_expires_at_unix_ns
                FROM event_dispatch_state
                WHERE claim_token = ?
                """,
                (token,),
            ).fetchone()
            if (
                row is None
                or row[1] != IngressDispatchStatus.CLAIMED.value
                or row[2] != token
                or row[3] is None
                or row[3] <= observed_ns
            ):
                connection.rollback()
                raise IngressClaimLostError(
                    "dispatch completion rejected because the claim is no longer active"
                )
            connection.execute(
                """
                UPDATE event_dispatch_state
                SET status = 'processed',
                    claimed_by = NULL,
                    claim_token = NULL,
                    claimed_at_unix_ns = NULL,
                    lease_expires_at_unix_ns = NULL,
                    last_error = NULL,
                    processed_at_unix_ns = ?,
                    downstream_receipt_id = ?
                WHERE idempotency_key = ?
                """,
                (observed_ns, downstream_receipt, row[0]),
            )
            connection.commit()
        except IngressClaimLostError:
            raise
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise IngressDispatchError(f"dispatch completion failed: {exc}") from exc
        finally:
            connection.close()

        record = self.get(row[0])
        if record is None:
            raise IngressDispatchError("processed dispatch record disappeared after commit")
        return record

    def release(self, claim_token: str, error: str) -> IngressDispatchRecord:
        token = _nonempty(claim_token, "claim_token")
        error_text = _nonempty(error, "error")[:4_096]
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT idempotency_key, status, claim_token
                FROM event_dispatch_state
                WHERE claim_token = ?
                """,
                (token,),
            ).fetchone()
            if (
                row is None
                or row[1] != IngressDispatchStatus.CLAIMED.value
                or row[2] != token
            ):
                connection.rollback()
                raise IngressClaimLostError(
                    "dispatch failure cannot release a claim owned by another worker"
                )
            connection.execute(
                """
                UPDATE event_dispatch_state
                SET status = 'pending',
                    claimed_by = NULL,
                    claim_token = NULL,
                    claimed_at_unix_ns = NULL,
                    lease_expires_at_unix_ns = NULL,
                    last_error = ?
                WHERE idempotency_key = ?
                """,
                (error_text, row[0]),
            )
            connection.commit()
        except IngressClaimLostError:
            raise
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise IngressDispatchError(f"dispatch release failed: {exc}") from exc
        finally:
            connection.close()

        record = self.get(row[0])
        if record is None:
            raise IngressDispatchError("released dispatch record disappeared after commit")
        return record

    def get(self, idempotency_key: str) -> IngressDispatchRecord | None:
        key = _nonempty(idempotency_key, "idempotency_key")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        state.idempotency_key,
                        state.dispatch_id,
                        acknowledgements.receipt_id,
                        state.status,
                        state.claimed_by,
                        state.claim_token,
                        state.claimed_at_unix_ns,
                        state.lease_expires_at_unix_ns,
                        state.attempt_count,
                        state.last_error,
                        state.processed_at_unix_ns,
                        state.downstream_receipt_id
                    FROM event_dispatch_state AS state
                    JOIN event_acknowledgements AS acknowledgements
                      ON acknowledgements.idempotency_key = state.idempotency_key
                    WHERE state.idempotency_key = ?
                    """,
                    (key,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise IngressDispatchError(f"dispatch lookup failed: {exc}") from exc
        return None if row is None else _record(row)

    def stats(self) -> IngressDispatchStats:
        observed_ns = self._clock()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT status, COUNT(*)
                    FROM event_dispatch_state
                    GROUP BY status
                    """
                ).fetchall()
                expired_row = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM event_dispatch_state
                    WHERE status = 'claimed'
                      AND lease_expires_at_unix_ns <= ?
                    """,
                    (observed_ns,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise IngressDispatchError(f"dispatch stats failed: {exc}") from exc
        counts = {status: int(count) for status, count in rows}
        return IngressDispatchStats(
            pending=counts.get(IngressDispatchStatus.PENDING.value, 0),
            claimed=counts.get(IngressDispatchStatus.CLAIMED.value, 0),
            processed=counts.get(IngressDispatchStatus.PROCESSED.value, 0),
            expired_claims=int(expired_row[0]) if expired_row is not None else 0,
        )

    def _clock(self) -> int:
        observed_ns = self.clock_ns()
        if observed_ns < 0:
            raise ValueError("dispatch clock cannot return a negative value")
        return observed_ns

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                f"PRAGMA busy_timeout={int(self.timeout_seconds * 1_000)}"
            )
            return connection
        except sqlite3.Error as exc:
            raise IngressDispatchError(f"dispatch database connection failed: {exc}") from exc


class RuntimeIngressHandler(Protocol):
    """Court/router boundary for one durably accepted Event Protocol envelope."""

    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        """Return a durable downstream receipt for this stable dispatch ID."""
        ...


class DispatchCycleState(StrEnum):
    IDLE = "idle"
    PROCESSED = "processed"
    RETRY = "retry"
    CLAIM_LOST = "claim_lost"


@dataclass(frozen=True)
class DispatchCycleResult:
    state: DispatchCycleState
    claim: IngressDispatchClaim | None = None
    downstream_receipt_id: str | None = None
    error: str | None = None


class DurableIngressDispatcher:
    """Claims ingress events and completes them only after downstream acknowledgement."""

    def __init__(
        self,
        queue: SqliteIngressDispatchQueue,
        handler: RuntimeIngressHandler,
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
    ) -> None:
        self.queue = queue
        self.handler = handler
        self.worker_id = _nonempty(worker_id, "worker_id")
        _lease_nanoseconds(lease_seconds)
        self.lease_seconds = float(lease_seconds)

    def process_one(self) -> DispatchCycleResult:
        claim = self.queue.claim_next(
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claim is None:
            return DispatchCycleResult(state=DispatchCycleState.IDLE)

        try:
            downstream_receipt = self.handler.dispatch(
                claim.envelope,
                dispatch_id=claim.dispatch_id,
                ingress_receipt_id=claim.ingress_receipt_id,
            )
            downstream_receipt = _nonempty(
                downstream_receipt,
                "handler downstream receipt",
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            try:
                self.queue.release(claim.claim_token, error)
            except IngressClaimLostError as lost:
                return DispatchCycleResult(
                    state=DispatchCycleState.CLAIM_LOST,
                    claim=claim,
                    error=f"{error}; {lost}",
                )
            return DispatchCycleResult(
                state=DispatchCycleState.RETRY,
                claim=claim,
                error=error,
            )

        try:
            self.queue.complete(claim.claim_token, downstream_receipt)
        except IngressClaimLostError as exc:
            return DispatchCycleResult(
                state=DispatchCycleState.CLAIM_LOST,
                claim=claim,
                downstream_receipt_id=downstream_receipt,
                error=str(exc),
            )
        return DispatchCycleResult(
            state=DispatchCycleState.PROCESSED,
            claim=claim,
            downstream_receipt_id=downstream_receipt,
        )

    def drain_available(self, *, max_events: int | None = None) -> tuple[DispatchCycleResult, ...]:
        if max_events is not None and max_events < 0:
            raise ValueError("max_events must be non-negative")
        results: list[DispatchCycleResult] = []
        while max_events is None or len(results) < max_events:
            result = self.process_one()
            results.append(result)
            if result.state is not DispatchCycleState.PROCESSED:
                break
        return tuple(results)


def _record(row: tuple[object, ...]) -> IngressDispatchRecord:
    return IngressDispatchRecord(
        idempotency_key=str(row[0]),
        dispatch_id=str(row[1]),
        ingress_receipt_id=str(row[2]),
        status=IngressDispatchStatus(str(row[3])),
        claimed_by=None if row[4] is None else str(row[4]),
        claim_token=None if row[5] is None else str(row[5]),
        claimed_at_unix_ns=None if row[6] is None else int(row[6]),
        lease_expires_at_unix_ns=None if row[7] is None else int(row[7]),
        attempt_count=int(row[8]),
        last_error=None if row[9] is None else str(row[9]),
        processed_at_unix_ns=None if row[10] is None else int(row[10]),
        downstream_receipt_id=None if row[11] is None else str(row[11]),
    )


def _claim_token(
    *,
    dispatch_id: str,
    worker_id: str,
    claimed_at_unix_ns: int,
    attempt_count: int,
) -> str:
    material = (
        f"{dispatch_id}|{worker_id}|{claimed_at_unix_ns}|{attempt_count}"
    ).encode("utf-8")
    return "runtime-claim-" + sha256(material).hexdigest()


def _lease_nanoseconds(lease_seconds: float) -> int:
    if isinstance(lease_seconds, bool) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    lease_ns = int(float(lease_seconds) * 1_000_000_000)
    if lease_ns <= 0:
        raise ValueError("lease_seconds is too small to represent")
    return lease_ns


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()
