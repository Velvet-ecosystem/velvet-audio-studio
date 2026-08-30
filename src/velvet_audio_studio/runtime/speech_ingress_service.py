"""Assemble the existing Audio HTTP/dispatch machinery for inbound speech."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from velvet_audio_studio.runtime.acknowledgement_store import (
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.http_receiver import EventProtocolReceiver
from velvet_audio_studio.runtime.ingress_dispatch import (
    DurableIngressDispatcher,
    SqliteIngressDispatchQueue,
)
from velvet_audio_studio.runtime.speech_expression_ingress import (
    SpeechExpressionIngressHandler,
    SpeechOutputSink,
    SqliteSpeechDeliveryLedger,
)


@dataclass(frozen=True)
class SpeechIngressComponents:
    store: SqliteAcknowledgementStore
    receiver: EventProtocolReceiver
    queue: SqliteIngressDispatchQueue
    delivery_ledger: SqliteSpeechDeliveryLedger
    handler: SpeechExpressionIngressHandler
    dispatcher: DurableIngressDispatcher


def build_speech_ingress_components(
    database: str | Path,
    output_service: SpeechOutputSink,
    *,
    endpoint_path: str = "/v1/speech-expressions",
    health_path: str = "/health",
    max_request_bytes: int = 65_536,
    bearer_token_file: str | Path | None = None,
    worker_id: str = "audio-speech-ingress",
    lease_seconds: float = 30.0,
) -> SpeechIngressComponents:
    """Build durable accept -> claim -> validate -> speak components.

    The existing acknowledgement store and ingress queue own transport durability.
    The speech delivery ledger adds only the acoustic-attempt truth needed to
    prevent ambiguous replay. All three use the same SQLite database file.
    """

    store = SqliteAcknowledgementStore(database)
    receiver = EventProtocolReceiver(
        store,
        endpoint_path=endpoint_path,
        health_path=health_path,
        max_request_bytes=max_request_bytes,
        bearer_token_file=bearer_token_file,
    )
    queue = SqliteIngressDispatchQueue(database)
    delivery_ledger = SqliteSpeechDeliveryLedger(database)
    handler = SpeechExpressionIngressHandler(output_service, delivery_ledger)
    dispatcher = DurableIngressDispatcher(
        queue,
        handler,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    return SpeechIngressComponents(
        store=store,
        receiver=receiver,
        queue=queue,
        delivery_ledger=delivery_ledger,
        handler=handler,
        dispatcher=dispatcher,
    )
