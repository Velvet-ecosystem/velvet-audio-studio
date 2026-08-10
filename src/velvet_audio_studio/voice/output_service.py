"""Local speech-output composition from approved text to Studio-owned playback."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

from velvet_audio_studio.contracts import AudioPriority, StudioRequest
from velvet_audio_studio.playback_engine import (
    StudioPlaybackResult,
    StudioSpeechPlaybackEngine,
)
from velvet_audio_studio.runtime.output_evidence import AudioOutputEvidenceEmitter
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
    expression_id: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.requester.strip():
            raise ValueError("speech output requester must be non-empty")
        if not self.purpose.strip():
            raise ValueError("speech output purpose must be non-empty")
        if not self.request_id.strip():
            raise ValueError("speech output request_id must be non-empty")
        if self.expression_id is not None and not self.expression_id.strip():
            raise ValueError("speech output expression_id must be non-empty when present")
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
    evidence_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _FailureState:
    output_event_id: str
    stage: str
    request_id: str


class LocalSpeechOutputService:
    """Turn already-approved wording into local, leased, multichannel speech.

    This service does not decide what Velvet may say or do. It resolves bounded
    acoustic delivery, synthesizes local PCM, obtains a Studio channel lease,
    hands the PCM to the single-owner playback engine, and emits privacy-bounded
    operational evidence through the shared Runtime journal when configured.
    """

    def __init__(
        self,
        synthesizer: SpeechSynthesizer,
        session_manager: StudioSessionManager,
        playback_engine: StudioSpeechPlaybackEngine,
        *,
        default_output_channels: tuple[int, ...],
        evidence_emitter: AudioOutputEvidenceEmitter | None = None,
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
        self.evidence_emitter = evidence_emitter
        self._failure_lock = Lock()
        self._last_failure: _FailureState | None = None

    def speak(self, request: SpeechOutputRequest) -> SpeechOutputResult:
        profile = select_delivery_profile(request.delivery)
        priority = max(request.priority, _minimum_priority(request.delivery))
        evidence_ids: list[str] = []
        recovery_candidate = self._current_failure()

        try:
            synthesized = self.synthesizer.synthesize(
                SpeechSynthesisRequest(
                    text=request.text,
                    profile_id=profile.profile_id,
                    speaker_id=request.speaker_id,
                )
            )
        except Exception as exc:
            self._record_failure(
                request=request,
                priority=priority,
                profile_id=profile.profile_id,
                model_id=None,
                output_channels=(),
                stage="synthesis",
                error=exc,
                evidence_ids=evidence_ids,
            )
            raise

        outputs = request.output_channels or self.default_output_channels
        studio_request = StudioRequest(
            requester=request.requester.strip(),
            purpose=request.purpose.strip(),
            priority=priority,
            output_channels=len(outputs),
            preferred_output_channels=outputs,
            allow_preemption=True,
            request_id=request.request_id,
            metadata={
                "source": "local_tts",
                "delivery_profile": profile.profile_id,
                "model_id": synthesized.model_id,
                "expression_id": request.expression_id,
                "command_authority": False,
            },
        )
        try:
            booking = self.session_manager.book_with_result(studio_request)
        except Exception as exc:
            self._record_failure(
                request=request,
                priority=priority,
                profile_id=profile.profile_id,
                model_id=synthesized.model_id,
                output_channels=outputs,
                stage="booking",
                error=exc,
                evidence_ids=evidence_ids,
            )
            raise

        lease = booking.lease
        emitter = self.evidence_emitter
        if emitter is not None:
            event = emitter.booked(
                request_id=request.request_id,
                priority=priority,
                output_channels=lease.output_channels,
                expression_id=request.expression_id,
                profile_id=profile.profile_id,
                model_id=synthesized.model_id,
                displaced_request_ids=tuple(
                    displaced.request_id for displaced in booking.displaced_leases
                ),
            )
            evidence_ids.append(str(event.payload["output_event_id"]))
            event = emitter.started(
                request_id=request.request_id,
                priority=priority,
                output_channels=lease.output_channels,
                expression_id=request.expression_id,
                profile_id=profile.profile_id,
                model_id=synthesized.model_id,
                source_sample_rate_hz=synthesized.sample_rate_hz,
                playback_sample_rate_hz=self.playback_engine.sink.sample_rate_hz,
                source_frames=synthesized.frame_count,
            )
            evidence_ids.append(str(event.payload["output_event_id"]))

        try:
            playback = self.playback_engine.play_speech(synthesized, lease)
        except Exception as exc:
            self._record_failure(
                request=request,
                priority=priority,
                profile_id=profile.profile_id,
                model_id=synthesized.model_id,
                output_channels=lease.output_channels,
                stage="playback",
                error=exc,
                evidence_ids=evidence_ids,
            )
            raise
        finally:
            self.session_manager.release(studio_request.request_id)

        if emitter is not None:
            if playback.preempted:
                preempted_by = playback.preempted_by_request_id
                if not preempted_by:
                    raise RuntimeError(
                        "preempted playback did not preserve the preempting request identity"
                    )
                event = emitter.preempted(
                    request_id=request.request_id,
                    priority=priority,
                    output_channels=playback.output_channels,
                    expression_id=request.expression_id,
                    profile_id=profile.profile_id,
                    model_id=synthesized.model_id,
                    playback_sample_rate_hz=playback.playback_sample_rate_hz,
                    frames_written=playback.frames_written,
                    playback_duration_ms=playback.playback_duration_ms,
                    preempted_by_request_id=preempted_by,
                )
                evidence_ids.append(str(event.payload["output_event_id"]))
            else:
                event = emitter.completed(
                    request_id=request.request_id,
                    priority=priority,
                    output_channels=playback.output_channels,
                    expression_id=request.expression_id,
                    profile_id=profile.profile_id,
                    model_id=synthesized.model_id,
                    playback_sample_rate_hz=playback.playback_sample_rate_hz,
                    frames_written=playback.frames_written,
                    playback_duration_ms=playback.playback_duration_ms,
                )
                evidence_ids.append(str(event.payload["output_event_id"]))
                if recovery_candidate is not None and self._clear_failure_if(
                    recovery_candidate
                ):
                    event = emitter.recovered(
                        request_id=request.request_id,
                        priority=priority,
                        output_channels=playback.output_channels,
                        expression_id=request.expression_id,
                        profile_id=profile.profile_id,
                        model_id=synthesized.model_id,
                        recovered_from_event_id=recovery_candidate.output_event_id,
                        recovered_from_stage=recovery_candidate.stage,
                    )
                    evidence_ids.append(str(event.payload["output_event_id"]))

        return SpeechOutputResult(
            profile_id=profile.profile_id,
            priority=priority,
            synthesized=synthesized,
            playback=playback,
            evidence_event_ids=tuple(evidence_ids),
        )

    def close(self) -> None:
        self.playback_engine.close()
        self.synthesizer.close()

    def _record_failure(
        self,
        *,
        request: SpeechOutputRequest,
        priority: AudioPriority,
        profile_id: str,
        model_id: str | None,
        output_channels: tuple[int, ...],
        stage: str,
        error: Exception,
        evidence_ids: list[str],
    ) -> None:
        emitter = self.evidence_emitter
        if emitter is None:
            return
        event = emitter.failed(
            request_id=request.request_id,
            priority=priority,
            output_channels=output_channels,
            expression_id=request.expression_id,
            profile_id=profile_id,
            model_id=model_id,
            failure_stage=stage,
            error=error,
        )
        event_id = str(event.payload["output_event_id"])
        evidence_ids.append(event_id)
        with self._failure_lock:
            self._last_failure = _FailureState(
                output_event_id=event_id,
                stage=stage,
                request_id=request.request_id,
            )

    def _current_failure(self) -> _FailureState | None:
        with self._failure_lock:
            return self._last_failure

    def _clear_failure_if(self, expected: _FailureState) -> bool:
        with self._failure_lock:
            if self._last_failure != expected:
                return False
            self._last_failure = None
            return True


def _minimum_priority(delivery: DeliveryContext) -> AudioPriority:
    if delivery.severity == "emergency":
        return AudioPriority.SAFETY
    if delivery.severity in {"warning", "critical"}:
        return AudioPriority.SYSTEM_ALERT
    return AudioPriority.VELVET_VOICE
