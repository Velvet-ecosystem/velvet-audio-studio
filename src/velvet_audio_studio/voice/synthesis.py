from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


MAX_TTS_TEXT_CHARS = 4096


class SpeechSynthesisError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechSynthesisRequest:
    text: str
    profile_id: str = "owner_default"
    speaker_id: int | None = None

    def __post_init__(self) -> None:
        normalized = " ".join(self.text.split())
        if not normalized:
            raise ValueError("speech synthesis text must be non-empty")
        if len(normalized) > MAX_TTS_TEXT_CHARS:
            raise ValueError(
                f"speech synthesis text exceeds {MAX_TTS_TEXT_CHARS} characters"
            )
        if not self.profile_id.strip():
            raise ValueError("speech synthesis profile_id must be non-empty")
        if self.speaker_id is not None and self.speaker_id < 0:
            raise ValueError("speech synthesis speaker_id cannot be negative")
        object.__setattr__(self, "text", normalized)
        object.__setattr__(self, "profile_id", self.profile_id.strip())


@dataclass(frozen=True)
class SynthesizedSpeech:
    model_id: str
    profile_id: str
    sample_rate_hz: int
    sample_width_bytes: int
    channels: int
    pcm_bytes: bytes
    text_char_count: int

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if not self.profile_id.strip():
            raise ValueError("profile_id must be non-empty")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.sample_width_bytes <= 0:
            raise ValueError("sample_width_bytes must be positive")
        if self.channels <= 0:
            raise ValueError("channels must be positive")
        if not self.pcm_bytes:
            raise ValueError("pcm_bytes must be non-empty")
        if self.text_char_count <= 0:
            raise ValueError("text_char_count must be positive")

    @property
    def frame_count(self) -> int:
        frame_width = self.sample_width_bytes * self.channels
        return len(self.pcm_bytes) // frame_width

    @property
    def duration_ms(self) -> float:
        return self.frame_count * 1000.0 / self.sample_rate_hz


class SpeechSynthesizer(Protocol):
    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesizedSpeech:
        ...

    def close(self) -> None:
        ...
