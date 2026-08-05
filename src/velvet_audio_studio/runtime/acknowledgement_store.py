"""Durable Runtime acknowledgement ledger for Event Protocol envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import sqlite3
from time import time_ns
from typing import Callable

from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    encode_event_protocol_envelope,
)


class AcknowledgementStoreError(RuntimeError):
    """Raised when the durable acknowledgement ledger cannot be used safely."""


class AcknowledgementConflictError(AcknowledgementStoreError):
    """Raised when one idempotency key is presented with different envelope bytes."""


@dataclass(frozen=True)
class DurableAcknowledgement:
    idempotency_key: str
    envelope_sha256: str
    receipt_id: str
    event_type: str
    source_id: str
    sequence: int
    occurred_at_monotonic_ns: int
    accepted_at_unix_ns: int
    last_seen_unix_ns: int
    duplicate_count: int
    duplicate: bool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_acknowledgements (
    idempotency_key TEXT PRIMARY KEY,
    envelope_sha256 TEXT NOT NULL,
    receipt_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    occurred_at_monotonic_ns INTEGER NOT NULL,
    canonical_envelope BLOB NOT NULL,
    accepted_at_unix_ns INTEGER NOT NULL,
    last_seen_unix_ns INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_event_acknowledgements_source_sequence
ON event_acknowledgements(source_id, sequence);
"""


class SqliteAcknowledgementStore:
    """Atomically records accepted envelopes and returns stable replay receipts."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock_ns: Callable[[], int] = time_ns,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("acknowledgement store timeout must be positive")
        self.path = Path(path).expanduser().resolve()
        self.clock_ns = clock_ns
        self.timeout_seconds = float(timeout_seconds)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript(_SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            raise AcknowledgementStoreError(
                f"acknowledgement store could not be initialized at {self.path}: {exc}"
            ) from exc

    def acknowledge(
        self,
        idempotency_key: str,
        envelope: EventProtocolEnvelope,
    ) -> DurableAcknowledgement:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency key cannot be empty")
        canonical = encode_event_protocol_envelope(envelope)
        envelope_digest = sha256(canonical).hexdigest()
        receipt_id = _receipt_id(key)
        observed_ns = self.clock_ns()
        if observed_ns < 0:
            raise ValueError("acknowledgement clock cannot return a negative value")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT idempotency_key, envelope_sha256, receipt_id, event_type,
                       source_id, sequence, occurred_at_monotonic_ns,
                       accepted_at_unix_ns, last_seen_unix_ns, duplicate_count
                FROM event_acknowledgements
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if row is not None:
                if row[1] != envelope_digest:
                    connection.rollback()
                    raise AcknowledgementConflictError(
                        "idempotency key already belongs to different envelope bytes"
                    )
                connection.execute(
                    """
                    UPDATE event_acknowledgements
                    SET last_seen_unix_ns = ?, duplicate_count = duplicate_count + 1
                    WHERE idempotency_key = ?
                    """,
                    (observed_ns, key),
                )
                connection.commit()
                return DurableAcknowledgement(
                    idempotency_key=row[0],
                    envelope_sha256=row[1],
                    receipt_id=row[2],
                    event_type=row[3],
                    source_id=row[4],
                    sequence=row[5],
                    occurred_at_monotonic_ns=row[6],
                    accepted_at_unix_ns=row[7],
                    last_seen_unix_ns=observed_ns,
                    duplicate_count=row[9] + 1,
                    duplicate=True,
                )

            connection.execute(
                """
                INSERT INTO event_acknowledgements (
                    idempotency_key,
                    envelope_sha256,
                    receipt_id,
                    event_type,
                    source_id,
                    sequence,
                    occurred_at_monotonic_ns,
                    canonical_envelope,
                    accepted_at_unix_ns,
                    last_seen_unix_ns,
                    duplicate_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    key,
                    envelope_digest,
                    receipt_id,
                    envelope.event_type,
                    envelope.source_id,
                    envelope.sequence,
                    envelope.occurred_at_monotonic_ns,
                    canonical,
                    observed_ns,
                    observed_ns,
                ),
            )
            connection.commit()
            return DurableAcknowledgement(
                idempotency_key=key,
                envelope_sha256=envelope_digest,
                receipt_id=receipt_id,
                event_type=envelope.event_type,
                source_id=envelope.source_id,
                sequence=envelope.sequence,
                occurred_at_monotonic_ns=envelope.occurred_at_monotonic_ns,
                accepted_at_unix_ns=observed_ns,
                last_seen_unix_ns=observed_ns,
                duplicate_count=0,
                duplicate=False,
            )
        except AcknowledgementConflictError:
            raise
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise AcknowledgementStoreError(
                f"acknowledgement store transaction failed: {exc}"
            ) from exc
        finally:
            connection.close()

    def get(self, idempotency_key: str) -> DurableAcknowledgement | None:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency key cannot be empty")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT idempotency_key, envelope_sha256, receipt_id, event_type,
                           source_id, sequence, occurred_at_monotonic_ns,
                           accepted_at_unix_ns, last_seen_unix_ns, duplicate_count
                    FROM event_acknowledgements
                    WHERE idempotency_key = ?
                    """,
                    (key,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AcknowledgementStoreError(
                f"acknowledgement store lookup failed: {exc}"
            ) from exc
        if row is None:
            return None
        return DurableAcknowledgement(
            idempotency_key=row[0],
            envelope_sha256=row[1],
            receipt_id=row[2],
            event_type=row[3],
            source_id=row[4],
            sequence=row[5],
            occurred_at_monotonic_ns=row[6],
            accepted_at_unix_ns=row[7],
            last_seen_unix_ns=row[8],
            duplicate_count=row[9],
            duplicate=row[9] > 0,
        )

    def count(self) -> int:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM event_acknowledgements"
                ).fetchone()
        except sqlite3.Error as exc:
            raise AcknowledgementStoreError(
                f"acknowledgement store count failed: {exc}"
            ) from exc
        return int(row[0]) if row is not None else 0

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={int(self.timeout_seconds * 1_000)}")
        return connection


def _receipt_id(idempotency_key: str) -> str:
    digest = sha256(f"velvet-runtime:{idempotency_key}".encode("utf-8")).hexdigest()
    return f"runtime-receipt-{digest[:32]}"
