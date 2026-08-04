import pytest

from velvet_audio_studio.capture.microphone_capture import analyze_capture


def test_capture_reports_levels_for_all_six_channels() -> None:
    packet = analyze_capture(
        (
            0.5, 0.25, 0.0, -0.25, 0.1, 0.0,
            -0.5, 0.25, 0.0, 0.25, -0.1, 0.0,
        ),
        captured_at_monotonic_ns=1_000_000_000,
        observed_at_monotonic_ns=1_010_000_000,
    )

    assert packet.event == "audio.capture.packet"
    assert packet.frames == 2
    assert packet.stale is False
    assert len(packet.channels) == 6
    assert packet.channels[0].logical_name == "driver_upper_mic"
    assert packet.channels[0].peak == 0.5
    assert packet.channels[4].peak == 0.1
    assert packet.degraded_reasons == ()


def test_capture_detects_clipping_staleness_and_mute() -> None:
    packet = analyze_capture(
        (0.99, 0.4, 0.0, 0.0, 0.0, 0.0),
        muted_channels=frozenset({1}),
        captured_at_monotonic_ns=1_000_000_000,
        observed_at_monotonic_ns=1_400_000_000,
    )

    assert packet.stale is True
    assert packet.channels[0].clipped is True
    assert packet.channels[1].muted is True
    assert packet.channels[1].peak == 0.0
    assert "driver_upper_mic clipping" in packet.degraded_reasons
    assert "capture packet stale" in packet.degraded_reasons


def test_capture_rejects_malformed_interleaved_buffer() -> None:
    with pytest.raises(ValueError, match="divide evenly"):
        analyze_capture((0.1, 0.2, 0.3))


def test_empty_capture_is_degraded_but_valid() -> None:
    packet = analyze_capture(())

    assert packet.frames == 0
    assert "capture packet empty" in packet.degraded_reasons
