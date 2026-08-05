from hashlib import sha256
from pathlib import Path
import sqlite3

from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    encode_event_protocol_envelope,
    event_protocol_idempotency_key,
)
from velvet_audio_studio.runtime.ingress_dispatch import (
    IngressDispatchStatus,
    SqliteIngressDispatchQueue,
)


_OLD_ACKNOWLEDGEMENT_SCHEMA = """
CREATE TABLE event_acknowledgements (
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
"""


def test_existing_acknowledgements_become_pending_dispatches(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    envelope = EventProtocolEnvelope(
        event_type="audio.capture.degraded",
        source_id="octo.capture.primary",
        sequence=3,
        occurred_at_monotonic_ns=3_000_000,
        payload={"reason": "clipping"},
    )
    canonical = encode_event_protocol_envelope(envelope)
    key = event_protocol_idempotency_key(envelope)
    receipt_id = "runtime-receipt-0123456789abcdef0123456789abcdef"

    with sqlite3.connect(database) as connection:
        connection.executescript(_OLD_ACKNOWLEDGEMENT_SCHEMA)
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
                sha256(canonical).hexdigest(),
                receipt_id,
                envelope.event_type,
                envelope.source_id,
                envelope.sequence,
                envelope.occurred_at_monotonic_ns,
                canonical,
                1_000,
                1_000,
            ),
        )

    queue = SqliteIngressDispatchQueue(database, clock_ns=lambda: 2_000)
    record = queue.get(key)

    assert record is not None
    assert record.status is IngressDispatchStatus.PENDING
    assert record.dispatch_id == "runtime-dispatch-0123456789abcdef0123456789abcdef"
    assert record.ingress_receipt_id == receipt_id
    assert record.attempt_count == 0
