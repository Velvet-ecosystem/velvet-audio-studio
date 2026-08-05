"""Durable poison-event evidence and quarantine for Runtime ingress dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import sqlite3
from time import time_ns
from typing import Callable

from velvet_audio_studio.runtime.ingress_dispatch import (
    IngressDispatchError,
    IngressDispatchStatus,
    SqliteIngressDispatchQueue,
)


_FAILURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_dispatch_failure_evidence (
    idempotency_key TEXT PRIMARY KEY
        REFERENCES event_acknowledgements(idempotency_key) ON DELETE CASCADE,
    failure_fingerprint TEXT NOT NULL,
    consecutive_count INTEGER NOT NULL,
    last_error TEXT NOT NULL,
    poison_reason TEXT,
    first_seen_unix_ns INTEGER NOT NULL,
    last_seen_unix_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS event_dispatch_quarantine (
    idempotency_key TEXT PRIMARY KEY
        REFERENCES event_acknowledgements(idempotency_key) ON DELETE CASCADE,
    dispatch_id TEXT NOT NULL UNIQUE,
    quarantine_receipt_id TEXT NOT NULL UNIQUE,
    failure_fingerprint TEXT NOT NULL,
    reason TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    quarantined_at_unix_ns INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class DispatchFailureEvidence:
    idempotency_key: str
    failure_fingerprint: str
    consecutive_count: int
    last_error: str
    poison_reason: str | None
    first_seen_unix_ns: int
    last_seen_unix_ns: int


@dataclass(frozen=True)
class QuarantinedDispatch:
    idempotency_key: str
    dispatch_id: str
    quarantine_receipt_id: str
    failure_fingerprint: str
    reason: str
    consecutive_failures: int
    quarantined_at_unix_ns: int


class QuarantinableIngressDispatchQueue(SqliteIngressDispatchQueue):
    """Dispatch queue with explicit failure evidence and terminal quarantine receipts."""

    def __init__(
        self,
        path: str,
        *,
        clock_ns: Callable[[], int] = time_ns,
        timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(
            path,
            clock_ns=clock_ns,
            timeout_seconds=timeout_seconds,
        )
        try:
            with self._connect() as connection:
                connection.executescript(_FAILURE_SCHEMA)
        except sqlite3.Error as exc:
            raise IngressDispatchError(
                f"dispatch quarantine schema could not be initialized: {exc}"
            ) from exc

    def record_failure(
        self,
        idempotency_key: str,
        error: str,
        *,
        poison_reason: str | None = None,
    ) -> DispatchFailureEvidence:
        key = _nonempty(idempotency_key, "idempotency_key")
        error_text = _nonempty(error, "error")[:4_096]
        reason = None if poison_reason is None else _nonempty(
            poison_reason,
            "poison_reason",
        )[:1_024]
        fingerprint = dispatch_failure_fingerprint(error_text)
        observed_ns = self._clock()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT failure_fingerprint, consecutive_count, first_seen_unix_ns
                FROM event_dispatch_failure_evidence
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if row is not None and str(row[0]) == fingerprint:
                consecutive = int(row[1]) + 1
                first_seen_ns = int(row[2])
            else:
                consecutive = 1
                first_seen_ns = observed_ns
            connection.execute(
                """
                INSERT INTO event_dispatch_failure_evidence (
                    idempotency_key,
                    failure_fingerprint,
                    consecutive_count,
                    last_error,
                    poison_reason,
                    first_seen_unix_ns,
                    last_seen_unix_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    failure_fingerprint = excluded.failure_fingerprint,
                    consecutive_count = excluded.consecutive_count,
                    last_error = excluded.last_error,
                    poison_reason = excluded.poison_reason,
                    first_seen_unix_ns = excluded.first_seen_unix_ns,
                    last_seen_unix_ns = excluded.last_seen_unix_ns
                """,
                (
                    key,
                    fingerprint,
                    consecutive,
                    error_text,
                    reason,
                    first_seen_ns,
                    observed_ns,
                ),
            )
            connection.commit()
        except sqlite3.Error as exc:
            _rollback(connection)
            raise IngressDispatchError(f"dispatch failure evidence could not be stored: {exc}") from exc
        finally:
            connection.close()
        return DispatchFailureEvidence(
            idempotency_key=key,
            failure_fingerprint=fingerprint,
            consecutive_count=consecutive,
            last_error=error_text,
            poison_reason=reason,
            first_seen_unix_ns=first_seen_ns,
            last_seen_unix_ns=observed_ns,
        )

    def quarantine_if_threshold(
        self,
        idempotency_key: str,
        *,
        failure_fingerprint: str,
        reason: str,
        minimum_consecutive_failures: int,
    ) -> QuarantinedDispatch | None:
        key = _nonempty(idempotency_key, "idempotency_key")
        fingerprint = _nonempty(failure_fingerprint, "failure_fingerprint")
        reason_text = _nonempty(reason, "quarantine reason")[:1_024]
        if minimum_consecutive_failures <= 0:
            raise ValueError("minimum_consecutive_failures must be positive")
        observed_ns = self._clock()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT idempotency_key, dispatch_id, quarantine_receipt_id,
                       failure_fingerprint, reason, consecutive_failures,
                       quarantined_at_unix_ns
                FROM event_dispatch_quarantine
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _quarantine_record(existing)

            row = connection.execute(
                """
                SELECT state.dispatch_id, state.status, state.claim_token,
                       evidence.failure_fingerprint, evidence.consecutive_count
                FROM event_dispatch_state AS state
                JOIN event_dispatch_failure_evidence AS evidence
                  ON evidence.idempotency_key = state.idempotency_key
                WHERE state.idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            if (
                str(row[1]) != IngressDispatchStatus.PENDING.value
                or row[2] is not None
                or str(row[3]) != fingerprint
                or int(row[4]) < minimum_consecutive_failures
            ):
                connection.commit()
                return None

            dispatch_id = str(row[0])
            consecutive = int(row[4])
            receipt = quarantine_receipt_id(dispatch_id, fingerprint)
            connection.execute(
                """
                INSERT INTO event_dispatch_quarantine (
                    idempotency_key,
                    dispatch_id,
                    quarantine_receipt_id,
                    failure_fingerprint,
                    reason,
                    consecutive_failures,
                    quarantined_at_unix_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    dispatch_id,
                    receipt,
                    fingerprint,
                    reason_text,
                    consecutive,
                    observed_ns,
                ),
            )
            connection.execute(
                """
                UPDATE event_dispatch_state
                SET status = 'processed',
                    claimed_by = NULL,
                    claim_token = NULL,
                    claimed_at_unix_ns = NULL,
                    lease_expires_at_unix_ns = NULL,
                    last_error = ?,
                    processed_at_unix_ns = ?,
                    downstream_receipt_id = ?
                WHERE idempotency_key = ?
                """,
                (
                    f"quarantined: {reason_text}",
                    observed_ns,
                    receipt,
                    key,
                ),
            )
            connection.commit()
            return QuarantinedDispatch(
                idempotency_key=key,
                dispatch_id=dispatch_id,
                quarantine_receipt_id=receipt,
                failure_fingerprint=fingerprint,
                reason=reason_text,
                consecutive_failures=consecutive,
                quarantined_at_unix_ns=observed_ns,
            )
        except sqlite3.Error as exc:
            _rollback(connection)
            raise IngressDispatchError(f"dispatch quarantine transaction failed: {exc}") from exc
        finally:
            connection.close()

    def get_quarantine(self, idempotency_key: str) -> QuarantinedDispatch | None:
        key = _nonempty(idempotency_key, "idempotency_key")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT idempotency_key, dispatch_id, quarantine_receipt_id,
                           failure_fingerprint, reason, consecutive_failures,
                           quarantined_at_unix_ns
                    FROM event_dispatch_quarantine
                    WHERE idempotency_key = ?
                    """,
                    (key,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise IngressDispatchError(f"dispatch quarantine lookup failed: {exc}") from exc
        return None if row is None else _quarantine_record(row)

    def quarantined_count(self) -> int:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM event_dispatch_quarantine"
                ).fetchone()
        except sqlite3.Error as exc:
            raise IngressDispatchError(f"dispatch quarantine count failed: {exc}") from exc
        return 0 if row is None else int(row[0])


def dispatch_failure_fingerprint(error: str) -> str:
    text = _nonempty(error, "error")
    return sha256(text.encode("utf-8")).hexdigest()


def quarantine_receipt_id(dispatch_id: str, failure_fingerprint: str) -> str:
    dispatch = _nonempty(dispatch_id, "dispatch_id")
    fingerprint = _nonempty(failure_fingerprint, "failure_fingerprint")
    digest = sha256(f"{dispatch}:{fingerprint}".encode("utf-8")).hexdigest()
    return f"runtime-quarantine-{digest[:32]}"


def _quarantine_record(row: tuple[object, ...]) -> QuarantinedDispatch:
    return QuarantinedDispatch(
        idempotency_key=str(row[0]),
        dispatch_id=str(row[1]),
        quarantine_receipt_id=str(row[2]),
        failure_fingerprint=str(row[3]),
        reason=str(row[4]),
        consecutive_failures=int(row[5]),
        quarantined_at_unix_ns=int(row[6]),
    )


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass
