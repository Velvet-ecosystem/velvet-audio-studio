from pathlib import Path
from threading import Thread

from velvet_audio_studio.runtime.acknowledgement_store import (
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    encode_event_protocol_envelope,
    event_protocol_idempotency_key,
)
from velvet_audio_studio.runtime.http_receiver import (
    EventProtocolReceiver,
    build_runtime_receiver_server,
)
from velvet_audio_studio.runtime.http_transport import HttpEventProtocolTransport


def _envelope() -> EventProtocolEnvelope:
    return EventProtocolEnvelope(
        event_type="audio.voice_input.ready",
        source_id="octo.capture.primary",
        sequence=12,
        occurred_at_monotonic_ns=4_000_000_000,
        payload={"selected_logical_name": "driver_upper_mic", "confidence": 1.0},
    )


def _headers(envelope: EventProtocolEnvelope, *, token: str | None = None) -> dict[str, str]:
    key = event_protocol_idempotency_key(envelope)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Idempotency-Key": key,
        "X-Velvet-Event-ID": key,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_receiver_accepts_once_and_replays_same_receipt(tmp_path: Path) -> None:
    store = SqliteAcknowledgementStore(tmp_path / "runtime-acks.sqlite3")
    receiver = EventProtocolReceiver(store)
    envelope = _envelope()
    body = encode_event_protocol_envelope(envelope)

    accepted = receiver.accept(
        method="POST",
        path="/v1/events",
        headers=_headers(envelope),
        body=body,
    )
    replay = receiver.accept(
        method="POST",
        path="/v1/events",
        headers=_headers(envelope),
        body=body,
    )

    accepted_headers = dict(accepted.headers)
    replay_headers = dict(replay.headers)
    assert accepted.status == 202
    assert replay.status == 409
    assert accepted_headers["X-Velvet-Receipt-ID"] == replay_headers[
        "X-Velvet-Receipt-ID"
    ]
    assert store.count() == 1
    stored = store.get(event_protocol_idempotency_key(envelope))
    assert stored is not None
    assert stored.duplicate_count == 1

    health = receiver.health()
    assert health.status == 200
    assert b'"accepted_events":1' in health.body
    assert b'"status":"ready"' in health.body


def test_receiver_rejects_mismatched_key_and_unknown_fields(tmp_path: Path) -> None:
    store = SqliteAcknowledgementStore(tmp_path / "runtime-acks.sqlite3")
    receiver = EventProtocolReceiver(store)
    envelope = _envelope()
    bad_headers = _headers(envelope)
    bad_headers["Idempotency-Key"] = "0" * 64

    mismatch = receiver.accept(
        method="POST",
        path="/v1/events",
        headers=bad_headers,
        body=encode_event_protocol_envelope(envelope),
    )
    unknown_field = receiver.accept(
        method="POST",
        path="/v1/events",
        headers=_headers(envelope),
        body=(
            b'{"event_type":"audio.voice_input.ready",'
            b'"source_id":"octo.capture.primary","sequence":12,'
            b'"occurred_at_monotonic_ns":4000000000,"payload":{},"surprise":true}'
        ),
    )

    assert mismatch.status == 400
    assert b"event_id_headers_disagree" in mismatch.body
    assert unknown_field.status == 400
    assert b"unknown fields" in unknown_field.body
    assert store.count() == 0


def test_receiver_requires_configured_bearer_token(tmp_path: Path) -> None:
    token_path = tmp_path / "runtime.token"
    token_path.write_text("correct-token\n", encoding="utf-8")
    receiver = EventProtocolReceiver(
        SqliteAcknowledgementStore(tmp_path / "runtime-acks.sqlite3"),
        bearer_token_file=token_path,
    )
    envelope = _envelope()
    body = encode_event_protocol_envelope(envelope)

    missing = receiver.accept(
        method="POST",
        path="/v1/events",
        headers=_headers(envelope),
        body=body,
    )
    wrong = receiver.accept(
        method="POST",
        path="/v1/events",
        headers=_headers(envelope, token="wrong-token"),
        body=body,
    )
    accepted = receiver.accept(
        method="POST",
        path="/v1/events",
        headers=_headers(envelope, token="correct-token"),
        body=body,
    )

    assert missing.status == 401
    assert wrong.status == 401
    assert accepted.status == 202


def test_live_http_sender_and_receiver_share_receipt_across_replay(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "runtime.token"
    token_path.write_text("vehicle-lan-token", encoding="utf-8")
    store = SqliteAcknowledgementStore(tmp_path / "runtime-acks.sqlite3")
    receiver = EventProtocolReceiver(store, bearer_token_file=token_path)
    server = build_runtime_receiver_server("127.0.0.1", 0, receiver)
    thread = Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    host, port = server.server_address[:2]
    transport = HttpEventProtocolTransport(
        f"http://{host}:{port}/v1/events",
        timeout_seconds=1.0,
        bearer_token_file=token_path,
    )

    try:
        first = transport.publish_envelope(_envelope())
        replay = transport.publish_envelope(_envelope())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert first == replay
    assert first.startswith("runtime-receipt-")
    assert store.count() == 1
    stored = store.get(event_protocol_idempotency_key(_envelope()))
    assert stored is not None
    assert stored.duplicate_count == 1
