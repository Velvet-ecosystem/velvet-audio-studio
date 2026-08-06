from __future__ import annotations

from dataclasses import dataclass
import re

from velvet_audio_studio.voice.transcription import SpeechTranscript


_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


def _normalized_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("wake-name text must be a string")
    return " ".join(token.casefold() for token in _TOKEN_RE.findall(value))


@dataclass(frozen=True)
class WakeNameConfig:
    names: tuple[str, ...] = ("hey velvet", "velvet", "princess")

    def __post_init__(self) -> None:
        normalized = tuple(_normalized_text(name) for name in self.names)
        if not normalized or any(not name for name in normalized):
            raise ValueError("wake names must contain at least one non-empty name")
        if len(set(normalized)) != len(normalized):
            raise ValueError("wake names must be unique after normalization")
        object.__setattr__(
            self,
            "names",
            tuple(sorted(normalized, key=lambda name: (-len(name.split()), name))),
        )


@dataclass(frozen=True)
class WakeNameDecision:
    matched: bool
    wake_name: str | None
    request_text: str
    transcript_text: str
    reason: str


class WakeNameGate:
    """Release only transcripts explicitly addressed to a configured wake name."""

    def __init__(self, config: WakeNameConfig | None = None) -> None:
        self.config = config or WakeNameConfig()
        self._wake_tokens = tuple(
            (name, tuple(name.split())) for name in self.config.names
        )

    def evaluate(self, transcript: SpeechTranscript) -> WakeNameDecision:
        normalized = _normalized_text(transcript.text)
        tokens = tuple(normalized.split())
        if not tokens:
            return WakeNameDecision(
                matched=False,
                wake_name=None,
                request_text="",
                transcript_text=normalized,
                reason="transcript empty",
            )

        for name, wake_tokens in self._wake_tokens:
            if tokens[: len(wake_tokens)] == wake_tokens:
                request = " ".join(tokens[len(wake_tokens) :])
                return WakeNameDecision(
                    matched=True,
                    wake_name=name,
                    request_text=request,
                    transcript_text=normalized,
                    reason="wake name matched transcript prefix",
                )
        return WakeNameDecision(
            matched=False,
            wake_name=None,
            request_text="",
            transcript_text=normalized,
            reason="wake name not present at transcript start",
        )
