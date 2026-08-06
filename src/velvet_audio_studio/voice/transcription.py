from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from velvet_audio_studio.voice.utterance import VoiceUtterance


class SpeechTranscriptionError(RuntimeError):
    """Raised when an offline transcription engine cannot produce a result."""


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start_seconds: float
    end_seconds: float
    confidence: float

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("transcript word text must be non-empty")
        if not isfinite(self.start_seconds) or self.start_seconds < 0:
            raise ValueError("transcript word start must be finite and non-negative")
        if not isfinite(self.end_seconds) or self.end_seconds < self.start_seconds:
            raise ValueError("transcript word end must be finite and follow its start")
        if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("transcript word confidence must be within [0, 1]")


@dataclass(frozen=True)
class SpeechTranscript:
    utterance_id: str
    text: str
    words: tuple[TranscriptWord, ...]
    confidence: float
    model_id: str
    language: str
    recognizer_sample_rate_hz: int
    source_duration_ms: int

    def __post_init__(self) -> None:
        if not self.utterance_id.strip():
            raise ValueError("utterance_id must be non-empty")
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if not self.language.strip():
            raise ValueError("language must be non-empty")
        if self.recognizer_sample_rate_hz <= 0:
            raise ValueError("recognizer_sample_rate_hz must be positive")
        if self.source_duration_ms < 0:
            raise ValueError("source_duration_ms cannot be negative")
        if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("transcript confidence must be within [0, 1]")

    @property
    def empty(self) -> bool:
        return not self.text.strip()


class SpeechTranscriber(Protocol):
    """Local-only boundary for converting one completed utterance to text."""

    def open(self) -> None:
        ...

    def transcribe(self, utterance: VoiceUtterance) -> SpeechTranscript:
        ...

    def close(self) -> None:
        ...
