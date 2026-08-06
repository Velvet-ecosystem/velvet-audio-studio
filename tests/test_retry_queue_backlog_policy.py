from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.retry_queue import OrderedRetryQueue


def _event(name: str, sequence: int, occurred_ns: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=occurred_ns,
        packet_sequence=sequence,
        payload={"frames": 64},
    )


def test_queue_reports_backlog_health() -> None:
    queue = OrderedRetryQueue(max_pending=4)
    queue.enqueue(
        (
            _event("audio.capture.packet", 1, 1_000_000_000),
            _event("audio.capture.packet", 2, 1_100_000_000),
            _event("audio.capture.degraded", 2, 1_200_000_000),
        )
    )

    health = queue.health(observed_at_monotonic_ns=32_000_000_000)

    assert health.state == "warning"
    assert health.pending_count == 3
    assert health.capacity_ratio == 0.75
    assert "retry queue nearing capacity" in health.warning_reasons
    assert "retry queue contains over-age events" in health.warning_reasons


def test_queue_compaction_preserves_transition_order() -> None:
    queue = OrderedRetryQueue(max_pending=8)
    queue.enqueue(
        (
            _event("audio.capture.packet", 1, 1_000),
            _event("audio.capture.packet", 2, 2_000),
            _event("audio.capture.degraded", 2, 2_100),
            _event("audio.capture.packet", 3, 3_000),
            _event("audio.capture.packet", 4, 4_000),
            _event("audio.capture.recovered", 4, 4_100),
        )
    )

    result = queue.compact()

    assert result.removed_count == 2
    assert [event.event for event in queue.snapshot()] == [
        "audio.capture.packet.summary",
        "audio.capture.degraded",
        "audio.capture.packet.summary",
        "audio.capture.recovered",
    ]


def test_queue_replace_rejects_over_capacity_restore() -> None:
    queue = OrderedRetryQueue(max_pending=1)

    try:
        queue.replace(
            (
                _event("audio.capture.packet", 1, 1_000),
                _event("audio.capture.packet", 2, 2_000),
            )
        )
    except OverflowError as exc:
        assert "replacement exceeds" in str(exc)
    else:
        raise AssertionError("expected over-capacity restore to fail")
