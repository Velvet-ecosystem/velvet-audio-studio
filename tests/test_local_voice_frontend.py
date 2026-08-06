from velvet_audio_studio.capture.supervisor import CaptureSupervisor
from velvet_audio_studio.capture.voice_handoff import VoiceInputHandoff
from velvet_audio_studio.runtime.capture_pipeline import PublishedCapturePipeline
from velvet_audio_studio.runtime.publisher import AudioRuntimeBridge, InMemoryRuntimePublisher
from velvet_audio_studio.voice.front_end import (
    LocalVoiceFrontEnd,
    LocalVoiceFrontEndConfig,
)
from velvet_audio_studio.voice.utterance import UtteranceCaptureConfig
from velvet_audio_studio.voice.vad import (
    EnergyVoiceActivityDetector,
    VoiceActivityConfig,
    VoiceActivityState,
)


def ready(samples: tuple[float, ...], *, confidence: float = 0.8) -> VoiceInputHandoff:
    return VoiceInputHandoff(
        event="audio.voice_input.ready",
        source_packet_event="audio.capture.packet",
        selected_channel_index=0,
        selected_logical_name="driver_upper_mic",
        mono_samples=samples,
        raw_multichannel_samples=samples,
        confidence=confidence,
        degraded_reasons=(),
    )


def degraded() -> VoiceInputHandoff:
    return VoiceInputHandoff(
        event="audio.voice_input.degraded",
        source_packet_event="audio.capture.packet",
        selected_channel_index=None,
        selected_logical_name=None,
        mono_samples=(),
        raw_multichannel_samples=(0.1, 0.0),
        confidence=0.0,
        degraded_reasons=("capture session awaiting recovery",),
    )


def test_energy_vad_uses_activation_and_release_hysteresis() -> None:
    detector = EnergyVoiceActivityDetector(VoiceActivityConfig(
        activation_rms=0.1,
        deactivation_rms=0.05,
        activation_packets=2,
        release_packets=3,
    ))

    assert detector.process((0.2, -0.2)).event == "audio.voice_activity.silent"
    started = detector.process((0.2, -0.2))
    assert started.event == "audio.voice_activity.started"
    assert started.state is VoiceActivityState.ACTIVE

    assert detector.process((0.0, 0.0)).event == "audio.voice_activity.active"
    assert detector.process((0.0, 0.0)).event == "audio.voice_activity.active"
    ended = detector.process((0.0, 0.0))
    assert ended.event == "audio.voice_activity.ended"
    assert ended.state is VoiceActivityState.SILENT


def test_frontend_keeps_samples_local_and_emits_metadata_only() -> None:
    frontend = LocalVoiceFrontEnd(LocalVoiceFrontEndConfig(
        vad=VoiceActivityConfig(
            activation_rms=0.1,
            deactivation_rms=0.05,
            activation_packets=1,
            release_packets=2,
        ),
        utterance=UtteranceCaptureConfig(
            pre_roll_ms=0,
            minimum_duration_ms=0,
            maximum_duration_ms=1_000,
        ),
    ))

    started = frontend.process(
        ready((0.2, -0.2)),
        sample_rate_hz=1_000,
        occurred_at_monotonic_ns=1_000_000,
        packet_sequence=1,
    )
    assert [event.event for event in started.events] == [
        "audio.voice_activity.started"
    ]
    assert started.completed_utterance is None

    frontend.process(
        ready((0.0, 0.0)),
        sample_rate_hz=1_000,
        occurred_at_monotonic_ns=2_000_000,
        packet_sequence=2,
    )
    completed = frontend.process(
        ready((0.0, 0.0)),
        sample_rate_hz=1_000,
        occurred_at_monotonic_ns=3_000_000,
        packet_sequence=3,
    )

    assert [event.event for event in completed.events] == [
        "audio.voice_activity.ended",
        "audio.utterance.ready",
    ]
    utterance = completed.completed_utterance
    assert utterance is not None
    assert utterance.samples == (0.2, -0.2, 0.0, 0.0, 0.0, 0.0)
    metadata = completed.events[-1].payload
    assert metadata["frames"] == 6
    assert metadata["raw_samples_in_event"] is False
    assert "samples" not in metadata


def test_degraded_handoff_cancels_active_utterance() -> None:
    frontend = LocalVoiceFrontEnd(LocalVoiceFrontEndConfig(
        vad=VoiceActivityConfig(activation_packets=1, release_packets=2),
        utterance=UtteranceCaptureConfig(minimum_duration_ms=0),
    ))
    frontend.process(
        ready((0.2, -0.2)),
        sample_rate_hz=1_000,
        occurred_at_monotonic_ns=1,
        packet_sequence=1,
    )

    result = frontend.process(
        degraded(),
        sample_rate_hz=1_000,
        occurred_at_monotonic_ns=2,
        packet_sequence=2,
    )

    assert [event.event for event in result.events] == [
        "audio.voice_activity.cancelled",
        "audio.utterance.cancelled",
    ]
    assert result.completed_utterance is None
    assert result.utterance_active is False


def test_maximum_duration_finishes_a_truncated_utterance() -> None:
    frontend = LocalVoiceFrontEnd(LocalVoiceFrontEndConfig(
        vad=VoiceActivityConfig(activation_packets=1, release_packets=2),
        utterance=UtteranceCaptureConfig(
            pre_roll_ms=0,
            minimum_duration_ms=0,
            maximum_duration_ms=5,
        ),
    ))
    frontend.process(
        ready((0.2, 0.2, 0.2)),
        sample_rate_hz=1_000,
        occurred_at_monotonic_ns=1,
        packet_sequence=1,
    )
    result = frontend.process(
        ready((0.2, 0.2, 0.2)),
        sample_rate_hz=1_000,
        occurred_at_monotonic_ns=2,
        packet_sequence=2,
    )

    utterance = result.completed_utterance
    assert utterance is not None
    assert len(utterance.samples) == 5
    assert utterance.completion_reason == "maximum_duration"
    assert utterance.truncated is True
    assert [event.event for event in result.events] == ["audio.utterance.ready"]


def test_capture_pipeline_appends_voice_events_after_capture_events() -> None:
    publisher = InMemoryRuntimePublisher()
    frontend = LocalVoiceFrontEnd(LocalVoiceFrontEndConfig(
        vad=VoiceActivityConfig(activation_packets=1, release_packets=1),
        utterance=UtteranceCaptureConfig(
            pre_roll_ms=0,
            minimum_duration_ms=0,
            maximum_duration_ms=1_000,
        ),
    ))
    supervisor = CaptureSupervisor()
    pipeline = PublishedCapturePipeline(
        supervisor,
        AudioRuntimeBridge(publisher),
        frontend,
    )
    supervisor.start(occurred_at_monotonic_ns=1)

    first = pipeline.process_and_publish(
        (0.2, 0.0, 0.0, 0.0, 0.0, 0.0, -0.2, 0.0, 0.0, 0.0, 0.0, 0.0),
        captured_at_monotonic_ns=10,
        observed_at_monotonic_ns=20,
    )
    second = pipeline.process_and_publish(
        (0.0,) * 12,
        captured_at_monotonic_ns=30,
        observed_at_monotonic_ns=40,
    )

    assert first.voice_frontend is not None
    assert first.voice_frontend.events[0].event == "audio.voice_activity.started"
    assert second.voice_frontend is not None
    assert second.voice_frontend.completed_utterance is not None
    names = [event.event for event in publisher.events]
    assert names.index("audio.voice_input.ready") < names.index(
        "audio.voice_activity.started"
    )
    assert names[-2:] == [
        "audio.voice_activity.ended",
        "audio.utterance.ready",
    ]
