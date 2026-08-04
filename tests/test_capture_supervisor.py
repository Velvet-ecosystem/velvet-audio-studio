from velvet_audio_studio.capture.session import CaptureSessionState
from velvet_audio_studio.capture.supervisor import CaptureSupervisor


def _healthy_packet(level: float = 0.2) -> tuple[float, ...]:
    return (
        level,
        level / 2,
        0.0,
        0.0,
        0.0,
        0.0,
        -level,
        -level / 2,
        0.0,
        0.0,
        0.0,
        0.0,
    )


def test_supervisor_publishes_start_packet_active_and_handoff_events() -> None:
    supervisor = CaptureSupervisor()

    starting = supervisor.start(occurred_at_monotonic_ns=1_000_000_000)
    result = supervisor.process(
        _healthy_packet(),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    assert starting.event == "audio.capture.starting"
    assert supervisor.session.state is CaptureSessionState.ACTIVE
    assert [event.event for event in result.events] == [
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
    ]
    assert result.handoff.selected_logical_name == "driver_upper_mic"
    assert result.events[-1].payload["frames"] == 2
    assert result.events[0].payload["channels"][0]["logical_name"] == "driver_upper_mic"


def test_supervisor_reports_degradation_and_recovers_after_two_packets() -> None:
    supervisor = CaptureSupervisor(recovery_packets_required=2)
    supervisor.start(occurred_at_monotonic_ns=1_000_000_000)
    supervisor.process(
        _healthy_packet(),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    degraded = supervisor.process(
        (0.99, 0.0, 0.0, 0.0, 0.0, 0.0),
        captured_at_monotonic_ns=1_030_000_000,
        observed_at_monotonic_ns=1_040_000_000,
    )
    first_recovery = supervisor.process(
        _healthy_packet(),
        captured_at_monotonic_ns=1_050_000_000,
        observed_at_monotonic_ns=1_060_000_000,
    )
    recovered = supervisor.process(
        _healthy_packet(),
        captured_at_monotonic_ns=1_070_000_000,
        observed_at_monotonic_ns=1_080_000_000,
    )

    assert "audio.capture.degraded" in [event.event for event in degraded.events]
    assert degraded.handoff.event == "audio.voice_input.ready"
    assert "audio.capture.recovered" not in [event.event for event in first_recovery.events]
    assert "audio.capture.recovered" in [event.event for event in recovered.events]
    assert supervisor.session.state is CaptureSessionState.ACTIVE


def test_supervisor_refuses_stale_audio_and_publishes_degraded_handoff() -> None:
    supervisor = CaptureSupervisor()
    supervisor.start(occurred_at_monotonic_ns=1_000_000_000)

    result = supervisor.process(
        _healthy_packet(),
        captured_at_monotonic_ns=1_000_000_000,
        observed_at_monotonic_ns=1_400_000_000,
    )

    assert result.packet.stale is True
    assert result.handoff.event == "audio.voice_input.degraded"
    assert result.handoff.mono_samples == ()
    assert [event.event for event in result.events] == [
        "audio.capture.packet",
        "audio.capture.degraded",
        "audio.voice_input.degraded",
    ]


def test_supervisor_stop_emits_final_sequence_receipt() -> None:
    supervisor = CaptureSupervisor()
    supervisor.start(occurred_at_monotonic_ns=1_000_000_000)
    supervisor.process(
        _healthy_packet(),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    stopped = supervisor.stop(occurred_at_monotonic_ns=1_030_000_000)

    assert stopped.event == "audio.capture.stopped"
    assert stopped.packet_sequence == 1
    assert stopped.payload["current_state"] == "stopped"
