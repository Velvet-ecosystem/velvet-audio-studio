from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.publisher import (
    AudioRuntimeBridge,
    InMemoryRuntimePublisher,
)


def _event(name: str, sequence: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=1_000_000_000 + sequence,
        packet_sequence=sequence,
        payload={"sequence": sequence},
    )


def test_bridge_delivers_events_in_order_with_receipts() -> None:
    publisher = InMemoryRuntimePublisher()
    bridge = AudioRuntimeBridge(publisher)
    events = (
        _event("audio.capture.packet", 1),
        _event("audio.voice_input.ready", 1),
    )

    batch = bridge.deliver(events)

    assert publisher.events == list(events)
    assert batch.delivered_count == 2
    assert batch.failed_count == 0
    assert batch.degraded is False
    assert [receipt.downstream_receipt_id for receipt in batch.receipts] == [
        "runtime-audio-000001",
        "runtime-audio-000002",
    ]


def test_bridge_converts_transport_exception_into_failed_receipt() -> None:
    class FailingPublisher:
        def publish(self, event: RuntimeAudioEvent) -> str:
            raise ConnectionError("event bus offline")

    batch = AudioRuntimeBridge(FailingPublisher()).deliver((_event("audio.capture.degraded", 2),))

    assert batch.delivered_count == 0
    assert batch.failed_count == 1
    assert batch.degraded is True
    assert batch.receipts[0].delivered is False
    assert "event bus offline" in (batch.receipts[0].degraded_reason or "")


def test_bridge_rejects_empty_downstream_receipt_identifier() -> None:
    class EmptyReceiptPublisher:
        def publish(self, event: RuntimeAudioEvent) -> str:
            return ""

    batch = AudioRuntimeBridge(EmptyReceiptPublisher()).deliver((_event("audio.capture.active", 1),))

    assert batch.failed_count == 1
    assert batch.receipts[0].downstream_receipt_id is None
    assert "empty receipt identifier" in (batch.receipts[0].degraded_reason or "")


def test_one_failed_event_does_not_block_later_events() -> None:
    class IntermittentPublisher:
        def __init__(self) -> None:
            self.calls = 0

        def publish(self, event: RuntimeAudioEvent) -> str:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("first publish timed out")
            return "runtime-audio-recovered"

    batch = AudioRuntimeBridge(IntermittentPublisher()).deliver(
        (
            _event("audio.capture.packet", 3),
            _event("audio.voice_input.ready", 3),
        )
    )

    assert batch.failed_count == 1
    assert batch.delivered_count == 1
    assert batch.receipts[1].downstream_receipt_id == "runtime-audio-recovered"
