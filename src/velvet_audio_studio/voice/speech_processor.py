from __future__ import annotations

from dataclasses import dataclass

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.voice.transcription import (
    SpeechTranscript,
    SpeechTranscriber,
    SpeechTranscriptionError,
)
from velvet_audio_studio.voice.utterance import VoiceUtterance
from velvet_audio_studio.voice.wake_gate import (
    WakeNameDecision,
    WakeNameGate,
)


SPEECH_PROCESSOR_SOURCE_ID = "audio.speech_processor"


@dataclass(frozen=True)
class SpeechProcessingResult:
    transcript: SpeechTranscript | None
    wake: WakeNameDecision | None
    events: tuple[RuntimeAudioEvent, ...]
    error: str | None = None


class LocalSpeechProcessor:
    """Transcribe locally, then release only wake-addressed request text."""

    def __init__(
        self,
        transcriber: SpeechTranscriber,
        wake_gate: WakeNameGate | None = None,
    ) -> None:
        self.transcriber = transcriber
        self.wake_gate = wake_gate or WakeNameGate()

    def open(self) -> None:
        self.transcriber.open()

    def close(self) -> None:
        self.transcriber.close()

    def process(
        self,
        utterance: VoiceUtterance,
        *,
        occurred_at_monotonic_ns: int,
        packet_sequence: int,
    ) -> SpeechProcessingResult:
        try:
            transcript = self.transcriber.transcribe(utterance)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            return SpeechProcessingResult(
                transcript=None,
                wake=None,
                events=(
                    self._event(
                        "audio.transcription.failed",
                        occurred_at_monotonic_ns,
                        packet_sequence,
                        {
                            "utterance_id": utterance.utterance_id,
                            "error": error,
                            "raw_samples_in_event": False,
                        },
                    ),
                ),
                error=error,
            )

        wake = self.wake_gate.evaluate(transcript)
        events: list[RuntimeAudioEvent] = [
            self._event(
                "audio.transcription.completed",
                occurred_at_monotonic_ns,
                packet_sequence,
                {
                    "utterance_id": transcript.utterance_id,
                    "model_id": transcript.model_id,
                    "language": transcript.language,
                    "confidence": transcript.confidence,
                    "word_count": len(transcript.words),
                    "text_length": len(transcript.text),
                    "empty": transcript.empty,
                    "source_duration_ms": transcript.source_duration_ms,
                    "recognizer_sample_rate_hz": transcript.recognizer_sample_rate_hz,
                    "raw_samples_in_event": False,
                    "transcript_text_in_event": False,
                },
            )
        ]
        if wake.matched:
            events.append(
                self._event(
                    "audio.wake_name.matched",
                    occurred_at_monotonic_ns,
                    packet_sequence,
                    {
                        "utterance_id": transcript.utterance_id,
                        "wake_name": wake.wake_name,
                        "request_text": wake.request_text,
                        "request_text_length": len(wake.request_text),
                        "transcript_confidence": transcript.confidence,
                        "command_authority": False,
                        "raw_samples_in_event": False,
                        "full_transcript_in_event": False,
                    },
                )
            )
        else:
            events.append(
                self._event(
                    "audio.wake_name.not_matched",
                    occurred_at_monotonic_ns,
                    packet_sequence,
                    {
                        "utterance_id": transcript.utterance_id,
                        "reason": wake.reason,
                        "text_length": len(transcript.text),
                        "transcript_text_in_event": False,
                    },
                )
            )
        return SpeechProcessingResult(
            transcript=transcript,
            wake=wake,
            events=tuple(events),
        )

    @staticmethod
    def _event(
        name: str,
        occurred_at_monotonic_ns: int,
        packet_sequence: int,
        payload: dict[str, object],
    ) -> RuntimeAudioEvent:
        if occurred_at_monotonic_ns < 0:
            raise ValueError("occurred_at_monotonic_ns cannot be negative")
        if packet_sequence < 0:
            raise ValueError("packet_sequence cannot be negative")
        return RuntimeAudioEvent(
            event=name,
            source_id=SPEECH_PROCESSOR_SOURCE_ID,
            occurred_at_monotonic_ns=occurred_at_monotonic_ns,
            packet_sequence=packet_sequence,
            payload=payload,
        )
