from __future__ import annotations

import pytest

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.channel_registry import ChannelRegistry
from velvet_audio_studio.contracts import AudioPriority, StudioRequest
from velvet_audio_studio.pcm import encode_pcm16_le
from velvet_audio_studio.playback_engine import StudioSpeechPlaybackEngine
from velvet_audio_studio.session_manager import StudioSessionManager
from velvet_audio_studio.voice.delivery_profiles import DeliveryContext
from velvet_audio_studio.voice.output_service import (
    LocalSpeechOutputService,
    SpeechOutputRequest,
)
from velvet_audio_studio.voice.synthesis import (
    SpeechSynthesisRequest,
    SynthesizedSpeech,
)


class FakeSynthesizer:
    def __init__(self) -> None:
        self.requests: list[SpeechSynthesisRequest] = []
        self.closed = False

    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesizedSpeech:
        self.requests.append(request)
        return SynthesizedSpeech(
            model_id="velvet-test",
            profile_id=request.profile_id,
            sample_rate_hz=48_000,
            sample_width_bytes=2,
            channels=1,
            pcm_bytes=encode_pcm16_le((0.25, -0.25)),
            text_char_count=len(request.text),
        )

    def close(self) -> None:
        self.closed = True


class FakeSink:
    sample_rate_hz = 48_000
    channels = 8
    sample_format = AlsaPcmFormat.S16_LE
    period_frames = 2

    def __init__(self) -> None:
        self.opened = False
        self.payloads: list[bytes] = []

    def open(self) -> None:
        self.opened = True

    def write(self, payload: bytes) -> int:
        self.payloads.append(payload)
        return len(payload) // 16

    def close(self) -> None:
        self.opened = False


def _service() -> tuple[
    LocalSpeechOutputService,
    FakeSynthesizer,
    ChannelRegistry,
    FakeSink,
]:
    synthesizer = FakeSynthesizer()
    registry = ChannelRegistry(input_count=6, output_count=8)
    sink = FakeSink()
    service = LocalSpeechOutputService(
        synthesizer,
        StudioSessionManager(registry),
        StudioSpeechPlaybackEngine(sink),
        default_output_channels=(4,),
    )
    return service, synthesizer, registry, sink


def test_service_runs_approved_text_through_profile_synthesis_lease_and_playback() -> None:
    service, synthesizer, registry, sink = _service()

    result = service.speak(
        SpeechOutputRequest(
            text="Mister, systems nominal.",
            delivery=DeliveryContext(
                requested_profile_id="playful_social",
                severity="informational",
                social_allowed=True,
            ),
        )
    )

    assert synthesizer.requests[0].profile_id == "playful_social"
    assert result.profile_id == "playful_social"
    assert result.priority is AudioPriority.VELVET_VOICE
    assert result.playback.output_channels == (4,)
    assert result.playback.frames_written == 2
    assert sink.payloads
    assert registry.leases == ()


def test_emergency_delivery_forces_emergency_voice_and_safety_priority() -> None:
    service, synthesizer, registry, _ = _service()

    result = service.speak(
        SpeechOutputRequest(
            text="Driver unresponsive.",
            delivery=DeliveryContext(
                requested_profile_id="playful_social",
                severity="emergency",
                social_allowed=True,
            ),
            priority=AudioPriority.MUSIC,
            output_channels=(4, 6),
        )
    )

    assert synthesizer.requests[0].profile_id == "emergency"
    assert result.priority is AudioPriority.SAFETY
    assert result.playback.output_channels == (4, 6)
    assert registry.leases == ()


def test_emergency_speech_can_take_center_slot_from_lower_priority_lease() -> None:
    service, synthesizer, registry, _ = _service()
    low = registry.allocate(
        StudioRequest(
            requester="Navigation",
            purpose="prompt",
            priority=AudioPriority.NAVIGATION,
            output_channels=1,
            preferred_output_channels=(4,),
            request_id="nav-low",
        )
    )

    result = service.speak(
        SpeechOutputRequest(
            text="Driver unresponsive.",
            delivery=DeliveryContext(severity="emergency"),
            output_channels=(4,),
        )
    )

    assert low.request_id == "nav-low"
    assert synthesizer.requests[-1].profile_id == "emergency"
    assert result.priority is AudioPriority.SAFETY
    assert result.playback.output_channels == (4,)
    assert registry.release("nav-low") is None
    assert registry.leases == ()


def test_service_releases_channel_lease_when_playback_fails() -> None:
    synthesizer = FakeSynthesizer()
    registry = ChannelRegistry(input_count=6, output_count=8)

    class FailingPlayback:
        def play_speech(self, synthesized, lease):
            raise RuntimeError("speaker path failed")

        def close(self) -> None:
            return None

    service = LocalSpeechOutputService(
        synthesizer,
        StudioSessionManager(registry),
        FailingPlayback(),
        default_output_channels=(4,),
    )

    with pytest.raises(RuntimeError, match="speaker path failed"):
        service.speak(SpeechOutputRequest(text="test"))
    assert registry.leases == ()


def test_service_close_closes_both_playback_and_synthesizer() -> None:
    service, synthesizer, _, sink = _service()
    service.speak(SpeechOutputRequest(text="test"))
    assert sink.opened is True

    service.close()

    assert sink.opened is False
    assert synthesizer.closed is True
