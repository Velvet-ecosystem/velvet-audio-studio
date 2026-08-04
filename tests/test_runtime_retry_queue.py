from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.event_protocol import EventProtocolPublisher
from velvet_audio_studio.runtime.retry_queue import OrderedRetryQueue


def _event(name: str, sequence: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=1_000_000_000 + sequence,
        packet_sequence=sequence,
        payload={"sequence": sequence},
    )


class RecordingTransport:
    def __init__(self) -> None:
        self.envelopes = []

    def publish_envelope(self, envelope):
        self.envelopes.append(envelope)
        return f"event-protocol-{len(self.envelopes):04d}"


class FailingPublisher:
    def __init__(self, fail_count: int) -> None:
        self.fail_count = fail_count
        self.events = []

    def publish(self, event):
        if self.fail_count:
            self.fail_count -= 1
            raise ConnectionError("Runtime unavailable")
        self.events.append(event)
        return f"receipt-{len(self.events)}"


def test_event_protocol_adapter_preserves_audio_event_fields() -> None:
    transport = RecordingTransport()
    publisher = EventProtocolPublisher(transport)

    receipt = publisher.publish(_event("audio.capture.active", 7))

    assert receipt == "event-protocol-0001"
    envelope = transport.envelopes[0]
    assert envelope.event_type == "audio.capture.active"
    assert envelope.source_id == "octo.capture.primary"
    assert envelope.sequence == 7
    assert envelope.payload == {"sequence": 7}


def test_retry_queue_stops_at_first_failure_to_preserve_order() -> None:
    queue = OrderedRetryQueue()
    queue.enqueue((_event("first", 1), _event("second", 2), _event("third", 3)))
    publisher = FailingPublisher(fail_count=1)

    first_attempt = queue.deliver(publisher)

    assert first_attempt.failed_count == 1
    assert queue.status.pending_count == 3
    assert publisher.events == []

    second_attempt = queue.deliver(publisher)

    assert second_attempt.delivered_count == 3
    assert [event.event for event in publisher.events] == ["first", "second", "third"]
    assert queue.status.pending_count == 0


def test_retry_queue_reports_sequence_bounds_and_overflow() -> None:
    queue = OrderedRetryQueue(max_pending=2)
    queue.enqueue((_event("one", 4), _event("two", 9)))

    assert queue.status.oldest_packet_sequence == 4
    assert queue.status.newest_packet_sequence == 9

    try:
        queue.enqueue((_event("three", 10),))
    except OverflowError as exc:
        assert "full" in str(exc)
    else:
        raise AssertionError("expected retry queue overflow")
