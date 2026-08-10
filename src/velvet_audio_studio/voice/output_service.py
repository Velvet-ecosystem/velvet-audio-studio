"""Local speech-output composition from approved text to Studio-owned playback."""

from __future__ import annotations

from dataclasses import dataclass, field

from velvet_audio_studio.contracts import AudioPriority, StudioRequest
from velvet_audio_studio.playback_engine import (
    StudioPlaybackResult,
    StudioSpeechPlaybackEngine,
)
from velvet_audio_studio.session_manager import StudioSessionManager
from velvet_audio_studio.voice.delivery_profiles import (
    DeliveryContext,
    select_delivery_profile,
)
from velvet_audio_studio.voice.synthesis import (
    SpeechSynthesisRequest,
    SpeechSynthesizer,
    SynthesizedSpeech,
)


@dataclass(frozen=True)
class SpeechOutputRequest:
    text: str
    delivery: DeliveryContext = field(default_factory=DeliveryContext)
    priority: AudioPriority = AudioPriority.VELVET_VOICE
    output_channels: tuple[int, ...] = ()
    requester: str = "Velvet"
    purpose: str = "speech"
    speaker_id: int | None = None

    def __post_init__(self) -> None:
        if not self.requester.strip():
            raise ValueError("speech output requester must be non-empty")
        if not self.purpose.strip():
            raise ValueError("speech output purpose must be non-empty")
        if len(set(self.output_channels)) != len(self.output_channels):
            raise ValueError("speech output channels must be unique")
        if any(channel < 0 for channel in self.output_channels):
            raise ValueError("speech output channels cannot be negative")


@dataclass(frozen=True)
class SpeechOutputResult:
    profile_id: str
    priority: AudioPriority
    synthesized: SynthesizedSpeech
    playback: StudioPlaybackResult


class LocalSpeechOutputService:
    """Turn already-approved wording into local, leased, multichannel speech.

    This service does not decide what Velvet may say or do. It resolves bounded
    acoustic delivery, synthesizes local PCM, obtains a Studio channel lease,
    and hands the PCM to the single-owner playback engine.
    """

    def __init__(
        self,
        synthesizer: SpeechSynthesizer,
        session_manager: StudioSessionManager,
        playback_engine: StudioSpeechPlaybackEngine,
        *,
        default_output_channels: tuple[int, ...],
    ) -> None:
        if not default_output_channels:
            raise ValueError("default_output_channels cannot be empty")
        if len(set(default_output_channels)) != len(default_output_channels):
            raise ValueError("default_output_channels must be unique")
        if any(channel < 0 for channel in default_output_channels):
            raise ValueError("default_output_channels cannot contain negative indexes")
        self.synthesizer = synthesizer
        self.session_manager = session_manager
        self.playback_engine = playback_engine
        self.default_output_channels = default_output_channels

    def speak(self, request: SpeechOutputRequest) -> SpeechOutputResult:
        profile = select_delivery_profile(request.delivery)
        priority = max(request.priority, _minimum_priority(request.delivery))
        synthesized = self.synthesizer.synthesize(
            SpeechSynthesisRequest(
                text=request.text,
                profile_id=profile.profile_id,
                speaker_id=request.speaker_id,
            )
        )

        outputs = request.output_channels or self.default_output_channels
        studio_request = StudioRequest(
            requester=request.requester.strip(),
            purpose=request.purpose.strip(),
            priority=priority,
            output_channels=len(outputs),
            preferred_output_channels=outputs,
            metadata={
                "source": "local_tts",
                "delivery_profile": profile.profile_id,
                "model_id": synthesized.model_id,
                "command_authority": False,
            },
        )
        lease = self.session_manager.book(studio_request)
        try:
            playback = self.playback_engine.play_speech(synthesized, lease)
        finally:
            self.session_manager.release(studio_request.request_id)

        return SpeechOutputResult(
            profile_id=profile.profile_id,
            priority=priority,
            synthesized=synthesized,
            playback=playback,
        )

    def close(self) -> None:
        self.playback_engine.close()
        self.synthesizer.close()


def _minimum_priority(delivery: DeliveryContext) -> AudioPriority:
    if delivery.severity == "emergency":
        return AudioPriority.SAFETY
    if delivery.severity in {"warning", "critical"}:
        return AudioPriority.SYSTEM_ALERT
    return AudioPriority.VELVET_VOICE
