from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.runtime.backlog_policy import assess_backlog, compact_backlog


def _event(name: str, sequence: int, occurred_ns: int) -> RuntimeAudioEvent:
    return RuntimeAudioEvent(
        event=name,
        source_id="octo.capture.primary",
        occurred_at_monotonic_ns=occurred_ns,
        packet_sequence=sequence,
        payload={"frames": 128, "marker": sequence},
    )


def test_backlog_health_warns_for_capacity_and_age() -> None:
    events = tuple(_event("audio.capture.packet", index, 1_000_000_000) for index in range(8))

    health = assess_backlog(
        events,
        max_pending=10,
        observed_at_monotonic_ns=32_000_000_000,
        max_age_ms=30_000,
    )

    assert health.state == "warning"
    assert health.capacity_ratio == 0.8
    assert health.oldest_age_ms == 31_000
    assert "retry queue nearing capacity" in health.warning_reasons
    assert "retry queue contains over-age events" in health.warning_reasons


def test_backlog_health_becomes_critical_at_capacity() -> None:
    events = tuple(_event("audio.capture.packet", index, 1_000) for index in range(4))

    health = assess_backlog(events, max_pending=4, observed_at_monotonic_ns=2_000)

    assert health.state == "critical"
    assert "retry queue at capacity" in health.warning_reasons


def test_compaction_summarizes_only_consecutive_capture_packets() -> None:
    events = (
        _event("audio.capture.packet", 1, 1_000),
        _event("audio.capture.packet", 2, 2_000),
        _event("audio.capture.degraded", 2, 2_100),
        _event("audio.capture.packet", 3, 3_000),
        _event("audio.capture.packet", 4, 4_000),
        _event("audio.capture.recovered", 4, 4_100),
    )

    result = compact_backlog(events)

    assert [event.event for event in result.events] == [
        "audio.capture.packet.summary",
        "audio.capture.degraded",
        "audio.capture.packet.summary",
        "audio.capture.recovered",
    ]
    assert result.removed_count == 2
    assert result.summary_count == 2
    assert result.events[0].payload["summarized_count"] == 2
    assert result.events[0].payload["first_packet_sequence"] == 1
    assert result.events[0].payload["last_packet_sequence"] == 2


def test_compaction_never_merges_critical_transitions() -> None:
    events = (
        _event("audio.capture.starting", 0, 1_000),
        _event("audio.capture.active", 1, 2_000),
        _event("audio.voice_input.ready", 1, 2_100),
        _event("audio.capture.degraded", 2, 3_000),
        _event("audio.voice_input.degraded", 2, 3_100),
        _event("audio.capture.recovered", 4, 5_000),
        _event("audio.capture.stopped", 5, 6_000),
    )

    result = compact_backlog(events)

    assert result.events == events
    assert result.removed_count == 0
    assert result.summary_count == 0
