from __future__ import annotations

from itertools import count

import pytest

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.channel_registry import ChannelRegistry
from velvet_audio_studio.contracts import AudioPriority, StudioRequest
from velvet_audio_studio.pcm import encode_pcm16_le
from velvet_audio_studio.playback_engine import StudioSpeechPlaybackEngine
from velvet_audio_studio.runtime.output_evidence import (
    AUDIO_OUTPUT_BOOKED,
    AUDIO_OUTPUT_COMPLETED,
    AUDIO_OUTPUT_FAILED,
    AUDIO_OUTPUT_RECOVERED,
    AUDIO_OUTPUT_STARTED,
    AudioOutputEvidenceEmitter,
)
from velvet_audio_studio.session_manager import StudioSessionManager
from velvet_audio_studio.voice.delivery_profiles import DeliveryContext
from velvet_audio_studio.voice.output_service import (
    LocalSpeechOutputService,
    SpeechOutputRequest,
)
from velvet_audio_studio.voice.synthesis import SpeechSynthesisRequest, SynthesizedSpeech


class FakeSink:
    sample_rate_hz = 48_000
    channels = 8
    sample_format = AlsaPcmFormat.S16_LE
    period_frames = 2

    def __init__(self) -> None:
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def write(self, payload: bytes) -> int:
        return len(payload) // 16

    def close(self) -> None:
        self.opened = False


class FakeSynthesizer:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0

    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesizedSpeech:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError(f"failed while synthesizing private input: {request.text}")
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
        return None


def _service(*, fail_first: bool = False):
    published = []
    event_numbers = count(1)
    emitter = AudioOutputEvidenceEmitter(
        node_id="audio-01",
        publish_events=lambda batch: published.extend(batch),
        clock_ns=lambda: 100,
        id_factory=lambda: f"evidence-{next(event_numbers)}",
    )
    registry = ChannelRegistry(input_count=6, output_count=8)
    service = LocalSpeechOutputService(
        FakeSynthesizer(fail_first=fail_first),
        StudioSessionManager(registry),
        StudioSpeechPlaybackEngine(FakeSink()),
        default_output_channels=(4,),
        evidence_emitter=emitter,
    )
    return service, registry, published


def test_success_emits_booked_started_completed_without_spoken_text() -> None:
    service, registry, published = _service()

    result = service.speak(
        SpeechOutputRequest(
            text="Mister, systems nominal.",
            expression_id="expression-1",
            request_id="request-1",
        )
    )

    assert [event.event for event in published] == [
        AUDIO_OUTPUT_BOOKED,
        AUDIO_OUTPUT_STARTED,
        AUDIO_OUTPUT_COMPLETED,
    ]
    assert result.evidence_event_ids == (
        "evidence-1",
        "evidence-2",
        "evidence-3",
    )
    assert all("text" not in event.payload for event in published)
    assert all("pcm_bytes" not in event.payload for event in published)
    assert all(event.payload["expression_id"] == "expression-1" for event in published)
    assert registry.leases == ()


def test_emergency_booking_records_displaced_lower_priority_request() -> None:
    service, registry, published = _service()
    registry.allocate(
        StudioRequest(
            requester="Navigation",
            purpose="prompt",
            priority=AudioPriority.NAVIGATION,
            output_channels=1,
            preferred_output_channels=(4,),
            request_id="nav-low",
        )
    )

    service.speak(
        SpeechOutputRequest(
            text="Driver unresponsive.",
            delivery=DeliveryContext(severity="emergency"),
            request_id="safety-request",
        )
    )

    booked = published[0]
    assert booked.event == AUDIO_OUTPUT_BOOKED
    assert booked.payload["priority"] == int(AudioPriority.SAFETY)
    assert booked.payload["displaced_request_ids"] == ["nav-low"]
    assert registry.leases == ()


def test_failure_then_success_emits_recovery_without_copying_failure_input() -> None:
    service, registry, published = _service(fail_first=True)

    with pytest.raises(RuntimeError, match="private input"):
        service.speak(
            SpeechOutputRequest(
                text="This sentence must never enter the receipt stream.",
                expression_id="failed-expression",
                request_id="failed-request",
            )
        )

    assert [event.event for event in published] == [AUDIO_OUTPUT_FAILED]
    failed = published[0]
    assert failed.payload["reason"] == "synthesis failed: RuntimeError"
    assert "sentence must never" not in repr(failed.payload)

    service.speak(
        SpeechOutputRequest(
            text="Recovered.",
            expression_id="recovery-expression",
            request_id="recovery-request",
        )
    )

    assert [event.event for event in published] == [
        AUDIO_OUTPUT_FAILED,
        AUDIO_OUTPUT_BOOKED,
        AUDIO_OUTPUT_STARTED,
        AUDIO_OUTPUT_COMPLETED,
        AUDIO_OUTPUT_RECOVERED,
    ]
    recovered = published[-1]
    assert recovered.payload["recovered_from_event_id"] == failed.payload["output_event_id"]
    assert recovered.payload["recovered_from_stage"] == "synthesis"
    assert registry.leases == ()
