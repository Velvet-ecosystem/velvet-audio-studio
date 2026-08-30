"""Durable Runtime-to-Audio speech-expression dispatch boundary.

The HTTP acknowledgement proves only that Audio Studio durably accepted a
transport envelope. This module owns the later local attempt state so a crash or
retry cannot silently make Velvet speak the same expression twice.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import time_ns
from typing import Mapping, Protocol

from velvet_audio_studio.runtime.event_protocol import EventProtocolEnvelope
from velvet_audio_studio.voice.expression_event import (
    SPEECH_EXPRESSION_EVENT,
    speech_output_request_from_event,
)
from velvet_audio_studio.voice.output_service import SpeechOutputRequest


class SpeechExpressionIngressError(RuntimeError):
    """Raised when accepted speech cannot be dispatched safely."""


class SpeechDeliveryState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class SpeechDeliveryDecision:
    expression_id: str
    dispatch_id: str
    receipt_id: str
    state: SpeechDeliveryState
    should_speak: bool


class SpeechOutputSink(Protocol):
    def speak(self, request: SpeechOutputRequest) -> object:
        ...


def speech_output_request_from_envelope(
    envelope: EventProtocolEnvelope,
) -> SpeechOutputRequest:
    """Validate both the transport wrapper and nested shared speech contract."""

    if envelope.event_type != SPEECH_EXPRESSION_EVENT:
        raise ValueError("ingress event is not a speech expression")
    if set(envelope.payload) != {"speech_expression"}:
        raise ValueError("speech ingress payload must contain only speech_expression")
    nested = envelope.payload.get("speech_expression")
    if not isinstance(nested, Mapping):
        raise ValueError("speech_expression must be a mapping")
    request = speech_output_request_from_event(nested)
    if request.expression_id is None:
        raise ValueError("speech expression identity is required")
    return request


class SqliteSpeechDeliveryLedger:
    """Store speech-attempt state without persisting spoken text."""

    def __init__(self, path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("speech delivery timeout must be positive")
        self.path = Path(path).expanduser().resolve()
        self.timeout_seconds = float(timeout_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS speech_expression_delivery (
                        expression_id TEXT PRIMARY KEY,
                        dispatch_id TEXT NOT NULL UNIQUE,
                        event_sha256 TEXT NOT NULL,
                        state TEXT NOT NULL CHECK(state IN ('started', 'completed', 'uncertain')),
                        receipt_id TEXT NOT NULL UNIQUE,
                        started_at_unix_ns INTEGER NOT NULL,
                        finished_at_unix_ns INTEGER,
                        failure_class TEXT
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise SpeechExpressionIngressError(
                f"speech delivery ledger initialization failed: {exc}"
            ) from exc

    def begin(
        self,
        *,
        expression_id: str,
        dispatch_id: str,
        event_sha256: str,
    ) -> SpeechDeliveryDecision:
        expression = _nonempty(expression_id, "expression_id")
        dispatch = _nonempty(dispatch_id, "dispatch_id")
        digest = _digest(event_sha256)
        observed_ns = time_ns()
        receipt_id = _receipt_id(expression, digest)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT dispatch_id, event_sha256, state, receipt_id
                FROM speech_expression_delivery
                WHERE expression_id = ?
                """,
                (expression,),
            ).fetchone()
            if row is not None:
                existing_digest = str(row[1])
                existing_state = SpeechDeliveryState(str(row[2]))
                existing_receipt = str(row[3])
                if existing_digest != digest:
                    connection.rollback()
                    raise SpeechExpressionIngressError(
                        "expression_id was reused with different speech content"
                    )
                if existing_state is SpeechDeliveryState.STARTED:
                    connection.execute(
                        """
                        UPDATE speech_expression_delivery
                        SET state = 'uncertain',
                            finished_at_unix_ns = ?,
                            failure_class = 'recovered_started_attempt'
                        WHERE expression_id = ?
                        """,
                        (observed_ns, expression),
                    )
                    connection.commit()
                    return SpeechDeliveryDecision(
                        expression_id=expression,
                        dispatch_id=dispatch,
                        receipt_id=existing_receipt,
                        state=SpeechDeliveryState.UNCERTAIN,
                        should_speak=False,
                    )
                connection.commit()
                return SpeechDeliveryDecision(
                    expression_id=expression,
                    dispatch_id=dispatch,
                    receipt_id=existing_receipt,
                    state=existing_state,
                    should_speak=False,
                )

            connection.execute(
                """
                INSERT INTO speech_expression_delivery (
                    expression_id,
                    dispatch_id,
                    event_sha256,
                    state,
                    receipt_id,
                    started_at_unix_ns
                ) VALUES (?, ?, ?, 'started', ?, ?)
                """,
                (expression, dispatch, digest, receipt_id, observed_ns),
            )
            connection.commit()
            return SpeechDeliveryDecision(
                expression_id=expression,
                dispatch_id=dispatch,
                receipt_id=receipt_id,
                state=SpeechDeliveryState.STARTED,
                should_speak=True,
            )
        except SpeechExpressionIngressError:
            raise
        except sqlite3.IntegrityError as exc:
            _rollback(connection)
            raise SpeechExpressionIngressError(
                f"speech delivery identity conflict: {exc}"
            ) from exc
        except sqlite3.Error as exc:
            _rollback(connection)
            raise SpeechExpressionIngressError(
                f"speech delivery begin failed: {exc}"
            ) from exc
        finally:
            connection.close()

    def complete(self, expression_id: str) -> str:
        return self._finish(expression_id, SpeechDeliveryState.COMPLETED, None)

    def mark_uncertain(self, expression_id: str, failure_class: str) -> str:
        failure = _nonempty(failure_class, "failure_class")[:128]
        return self._finish(expression_id, SpeechDeliveryState.UNCERTAIN, failure)

    def state(self, expression_id: str) -> SpeechDeliveryState | None:
        expression = _nonempty(expression_id, "expression_id")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT state FROM speech_expression_delivery WHERE expression_id = ?",
                    (expression,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SpeechExpressionIngressError(
                f"speech delivery lookup failed: {exc}"
            ) from exc
        return None if row is None else SpeechDeliveryState(str(row[0]))

    def _finish(
        self,
        expression_id: str,
        state: SpeechDeliveryState,
        failure_class: str | None,
    ) -> str:
        expression = _nonempty(expression_id, "expression_id")
        observed_ns = time_ns()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, receipt_id FROM speech_expression_delivery WHERE expression_id = ?",
                (expression,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise SpeechExpressionIngressError("speech delivery attempt was not started")
            current = SpeechDeliveryState(str(row[0]))
            receipt_id = str(row[1])
            if current is not SpeechDeliveryState.STARTED:
                connection.commit()
                return receipt_id
            connection.execute(
                """
                UPDATE speech_expression_delivery
                SET state = ?, finished_at_unix_ns = ?, failure_class = ?
                WHERE expression_id = ?
                """,
                (state.value, observed_ns, failure_class, expression),
            )
            connection.commit()
            return receipt_id
        except SpeechExpressionIngressError:
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise SpeechExpressionIngressError(
                f"speech delivery completion failed: {exc}"
            ) from exc
        finally:
            connection.close()

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
            raise SpeechExpressionIngressError(
                f"speech delivery database connection failed: {exc}"
            ) from exc


class SpeechExpressionIngressHandler:
    """Revalidate one accepted expression and hand it to Audio-owned playback."""

    def __init__(
        self,
        output_service: SpeechOutputSink,
        ledger: SqliteSpeechDeliveryLedger,
    ) -> None:
        self.output_service = output_service
        self.ledger = ledger

    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        del ingress_receipt_id
        request = speech_output_request_from_envelope(envelope)
        nested = envelope.payload["speech_expression"]
        event_digest = _event_digest(nested)
        decision = self.ledger.begin(
            expression_id=request.expression_id,
            dispatch_id=dispatch_id,
            event_sha256=event_digest,
        )
        if not decision.should_speak:
            return decision.receipt_id

        deterministic_request = replace(request, request_id=dispatch_id)
        try:
            self.output_service.speak(deterministic_request)
        except Exception as exc:
            # Once an attempt has started we cannot safely know after every class
            # of crash or device error whether acoustic output partially occurred.
            # Preserve uncertainty and suppress automatic replay of this expression.
            return self.ledger.mark_uncertain(
                request.expression_id,
                type(exc).__name__,
            )
        return self.ledger.complete(request.expression_id)


def _event_digest(event: Mapping[str, object]) -> str:
    try:
        raw = json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpeechExpressionIngressError(
            f"speech expression is not canonical JSON data: {exc}"
        ) from exc
    return sha256(raw).hexdigest()


def _receipt_id(expression_id: str, event_sha256: str) -> str:
    material = f"{expression_id}|{event_sha256}".encode("utf-8")
    return "audio-speech-delivery-" + sha256(material).hexdigest()


def _digest(value: str) -> str:
    text = _nonempty(value, "event_sha256").casefold()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError("event_sha256 must be a lowercase SHA-256 digest")
    return text


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass
