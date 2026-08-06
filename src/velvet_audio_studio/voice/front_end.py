from __future__ import annotations

from dataclasses import dataclass

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.capture.voice_handoff import VoiceInputHandoff
from velvet_audio_studio.voice.utterance import (
    BoundedUtteranceCapture,
    UtteranceCaptureConfig,
    UtteranceCaptureResult,
    VoiceUtterance,
)
from velvet_audio_studio.voice.vad import (
    EnergyVoiceActivityDetector,
    VoiceActivityConfig,
    VoiceActivityDecision,
    VoiceActivityState,
)


VOICE_FRONTEND_SOURCE_ID = "audio.voice_frontend"


@dataclass(frozen=True)
class LocalVoiceFrontEndConfig:
    vad: VoiceActivityConfig = VoiceActivityConfig()
    utterance: UtteranceCaptureConfig = UtteranceCaptureConfig()


@dataclass(frozen=True)
class LocalVoiceFrontEndResult:
    decision: VoiceActivityDecision | None
    completed_utterance: VoiceUtterance | None
    events: tuple[RuntimeAudioEvent, ...]
    utterance_active: bool


class LocalVoiceFrontEnd:
    """Convert lifecycle-approved mono handoffs into local utterance objects."""

    def __init__(
        self,
        config: LocalVoiceFrontEndConfig = LocalVoiceFrontEndConfig(),
    ) -> None:
        self.config = config
        self.detector = EnergyVoiceActivityDetector(config.vad)
        self.utterances = BoundedUtteranceCapture(config.utterance)

    def process(
        self,
        handoff: VoiceInputHandoff,
        *,
        sample_rate_hz: int,
        occurred_at_monotonic_ns: int,
        packet_sequence: int,
    ) -> LocalVoiceFrontEndResult:
        if handoff.event != "audio.voice_input.ready":
            previous_state = self.detector.reset()
            cancellation = self.utterances.cancel(
                occurred_at_monotonic_ns=occurred_at_monotonic_ns,
                reason="capture handoff degraded",
            )
            events: list[RuntimeAudioEvent] = []
            if previous_state is VoiceActivityState.ACTIVE:
                events.append(self._event(
                    "audio.voice_activity.cancelled",
                    occurred_at_monotonic_ns,
                    packet_sequence,
                    {
                        "reason": "capture handoff degraded",
                        "handoff_reasons": handoff.degraded_reasons,
                    },
                ))
            events.extend(self._utterance_events(
                cancellation,
                occurred_at_monotonic_ns,
                packet_sequence,
            ))
            return LocalVoiceFrontEndResult(
                decision=None,
                completed_utterance=None,
                events=tuple(events),
                utterance_active=False,
            )

        selected = handoff.selected_logical_name
        if selected is None:
            raise ValueError("ready voice handoff must identify a microphone")
        decision = self.detector.process(handoff.mono_samples)
        capture = self.utterances.process(
            handoff.mono_samples,
            decision,
            sample_rate_hz=sample_rate_hz,
            occurred_at_monotonic_ns=occurred_at_monotonic_ns,
            selected_logical_name=selected,
            confidence=handoff.confidence,
        )

        events: list[RuntimeAudioEvent] = []
        if decision.event in {
            "audio.voice_activity.started",
            "audio.voice_activity.ended",
        }:
            events.append(self._event(
                decision.event,
                occurred_at_monotonic_ns,
                packet_sequence,
                {
                    "selected_logical_name": selected,
                    "confidence": handoff.confidence,
                    "rms": decision.rms,
                    "peak": decision.peak,
                },
            ))
        events.extend(self._utterance_events(
            capture,
            occurred_at_monotonic_ns,
            packet_sequence,
        ))
        return LocalVoiceFrontEndResult(
            decision=decision,
            completed_utterance=capture.completed,
            events=tuple(events),
            utterance_active=capture.active,
        )

    def stop(
        self,
        *,
        occurred_at_monotonic_ns: int,
        packet_sequence: int,
    ) -> tuple[RuntimeAudioEvent, ...]:
        previous_state = self.detector.reset()
        cancellation = self.utterances.cancel(
            occurred_at_monotonic_ns=occurred_at_monotonic_ns,
            reason="voice front end stopped",
        )
        events: list[RuntimeAudioEvent] = []
        if previous_state is VoiceActivityState.ACTIVE:
            events.append(self._event(
                "audio.voice_activity.cancelled",
                occurred_at_monotonic_ns,
                packet_sequence,
                {"reason": "voice front end stopped"},
            ))
        events.extend(self._utterance_events(
            cancellation,
            occurred_at_monotonic_ns,
            packet_sequence,
        ))
        return tuple(events)

    def _utterance_events(
        self,
        result: UtteranceCaptureResult,
        occurred_at_monotonic_ns: int,
        packet_sequence: int,
    ) -> tuple[RuntimeAudioEvent, ...]:
        if result.event is None:
            return ()
        payload: dict[str, object] = {}
        utterance = result.completed
        if utterance is not None:
            payload = {
                "utterance_id": utterance.utterance_id,
                "sample_rate_hz": utterance.sample_rate_hz,
                "frames": len(utterance.samples),
                "duration_ms": utterance.duration_ms,
                "selected_logical_name": utterance.selected_logical_name,
                "confidence": utterance.confidence,
                "completion_reason": utterance.completion_reason,
                "truncated": utterance.truncated,
                "raw_samples_in_event": False,
            }
        elif result.reason is not None:
            payload = {"reason": result.reason}
        return (
            self._event(
                result.event,
                occurred_at_monotonic_ns,
                packet_sequence,
                payload,
            ),
        )

    @staticmethod
    def _event(
        name: str,
        occurred_at_monotonic_ns: int,
        packet_sequence: int,
        payload: dict[str, object],
    ) -> RuntimeAudioEvent:
        return RuntimeAudioEvent(
            event=name,
            source_id=VOICE_FRONTEND_SOURCE_ID,
            occurred_at_monotonic_ns=occurred_at_monotonic_ns,
            packet_sequence=packet_sequence,
            payload=payload,
        )
