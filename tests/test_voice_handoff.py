from velvet_audio_studio.capture.microphone_capture import analyze_capture
from velvet_audio_studio.capture.voice_handoff import prepare_voice_handoff


def test_handoff_selects_strongest_healthy_microphone() -> None:
    packet = analyze_capture(
        (
            0.1, 0.4, 0.2, 0.0, 0.05, 0.0,
            -0.1, -0.4, -0.2, 0.0, -0.05, 0.0,
        ),
        captured_at_monotonic_ns=1_000_000_000,
        observed_at_monotonic_ns=1_010_000_000,
    )

    handoff = prepare_voice_handoff(packet)

    assert handoff.event == "audio.voice_input.ready"
    assert handoff.selected_channel_index == 1
    assert handoff.selected_logical_name == "passenger_upper_mic"
    assert handoff.mono_samples == (0.4, -0.4)
    assert handoff.raw_multichannel_samples == packet.interleaved_samples
    assert handoff.confidence == 1.0


def test_handoff_avoids_clipped_and_muted_microphones() -> None:
    packet = analyze_capture(
        (0.99, 0.0, 0.3, 0.0, 0.0, 0.0),
        muted_channels=frozenset({1}),
        captured_at_monotonic_ns=1_000_000_000,
        observed_at_monotonic_ns=1_010_000_000,
    )

    handoff = prepare_voice_handoff(packet)

    assert handoff.selected_channel_index == 2
    assert handoff.selected_logical_name == "rear_left_mic"


def test_stale_capture_cannot_be_handed_to_voice_recognition() -> None:
    packet = analyze_capture(
        (0.2, 0.1, 0.0, 0.0, 0.0, 0.0),
        captured_at_monotonic_ns=1_000_000_000,
        observed_at_monotonic_ns=1_400_000_000,
    )

    handoff = prepare_voice_handoff(packet)

    assert handoff.event == "audio.voice_input.degraded"
    assert handoff.selected_channel_index is None
    assert handoff.mono_samples == ()
    assert "no healthy microphone candidate" in handoff.degraded_reasons
