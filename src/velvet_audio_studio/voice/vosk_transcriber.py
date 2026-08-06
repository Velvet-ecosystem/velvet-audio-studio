from __future__ import annotations

from array import array
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
import sys
from typing import Any

from velvet_audio_studio.voice.transcription import (
    SpeechTranscript,
    SpeechTranscriptionError,
    TranscriptWord,
)
from velvet_audio_studio.voice.utterance import VoiceUtterance


@dataclass(frozen=True)
class VoskTranscriberConfig:
    model_path: Path
    recognizer_sample_rate_hz: int = 16_000
    language: str = "en-us"
    include_words: bool = True
    max_alternatives: int = 0
    log_level: int = -1
    grammar: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_path", Path(self.model_path).expanduser().resolve())
        if self.recognizer_sample_rate_hz <= 0:
            raise ValueError("recognizer_sample_rate_hz must be positive")
        if not self.language.strip():
            raise ValueError("language must be non-empty")
        if self.max_alternatives < 0:
            raise ValueError("max_alternatives cannot be negative")
        normalized_grammar = tuple(item.strip() for item in self.grammar)
        if any(not item for item in normalized_grammar):
            raise ValueError("grammar entries must be non-empty")
        object.__setattr__(self, "grammar", normalized_grammar)


VoskApiLoader = Callable[[], Any]


class VoskOfflineTranscriber:
    """Transcribe bounded local utterances through a lazily loaded Vosk model."""

    def __init__(
        self,
        config: VoskTranscriberConfig,
        *,
        api_loader: VoskApiLoader | None = None,
    ) -> None:
        self.config = config
        self._api_loader = api_loader or _load_vosk_api
        self._api: Any | None = None
        self._model: Any | None = None

    @property
    def open_state(self) -> bool:
        return self._model is not None

    def open(self) -> None:
        if self._model is not None:
            return
        if not self.config.model_path.is_dir():
            raise SpeechTranscriptionError(
                f"Vosk model directory was not found: {self.config.model_path}"
            )
        try:
            api = self._api_loader()
            set_log_level = getattr(api, "SetLogLevel", None)
            if callable(set_log_level):
                set_log_level(self.config.log_level)
            model = api.Model(model_path=str(self.config.model_path))
        except SpeechTranscriptionError:
            raise
        except Exception as exc:
            raise SpeechTranscriptionError(
                f"Vosk model could not be opened: {type(exc).__name__}: {exc}"
            ) from exc
        self._api = api
        self._model = model

    def transcribe(self, utterance: VoiceUtterance) -> SpeechTranscript:
        if self._model is None:
            self.open()
        assert self._api is not None
        assert self._model is not None

        samples = resample_linear(
            utterance.samples,
            source_rate_hz=utterance.sample_rate_hz,
            target_rate_hz=self.config.recognizer_sample_rate_hz,
        )
        pcm = encode_pcm16_le(samples)
        try:
            if self.config.grammar:
                recognizer = self._api.KaldiRecognizer(
                    self._model,
                    float(self.config.recognizer_sample_rate_hz),
                    json.dumps(self.config.grammar),
                )
            else:
                recognizer = self._api.KaldiRecognizer(
                    self._model,
                    float(self.config.recognizer_sample_rate_hz),
                )
            recognizer.SetWords(self.config.include_words)
            if self.config.max_alternatives:
                recognizer.SetMaxAlternatives(self.config.max_alternatives)
            recognizer.AcceptWaveform(pcm)
            raw_result = recognizer.FinalResult()
        except Exception as exc:
            raise SpeechTranscriptionError(
                f"Vosk recognition failed: {type(exc).__name__}: {exc}"
            ) from exc

        result = _decode_result(raw_result)
        text, words = _result_text_and_words(result)
        confidence = (
            sum(word.confidence for word in words) / len(words)
            if words
            else 0.0
        )
        return SpeechTranscript(
            utterance_id=utterance.utterance_id,
            text=text,
            words=words,
            confidence=confidence,
            model_id=self.config.model_path.name,
            language=self.config.language.strip(),
            recognizer_sample_rate_hz=self.config.recognizer_sample_rate_hz,
            source_duration_ms=utterance.duration_ms,
        )

    def close(self) -> None:
        self._model = None
        self._api = None


def _load_vosk_api() -> Any:
    try:
        import vosk
    except ImportError as exc:
        raise SpeechTranscriptionError(
            "Vosk is not installed; install the velvet-audio-studio speech extra"
        ) from exc
    return vosk


def resample_linear(
    samples: Sequence[float],
    *,
    source_rate_hz: int,
    target_rate_hz: int,
) -> tuple[float, ...]:
    if source_rate_hz <= 0 or target_rate_hz <= 0:
        raise ValueError("sample rates must be positive")
    normalized = tuple(float(sample) for sample in samples)
    if any(not isfinite(sample) for sample in normalized):
        raise ValueError("audio samples must be finite")
    if not normalized or source_rate_hz == target_rate_hz:
        return normalized

    output_length = max(1, round(len(normalized) * target_rate_hz / source_rate_hz))
    scale = source_rate_hz / target_rate_hz
    output: list[float] = []
    last_index = len(normalized) - 1
    for output_index in range(output_length):
        source_position = min(output_index * scale, last_index)
        left = int(source_position)
        right = min(left + 1, last_index)
        fraction = source_position - left
        output.append(
            normalized[left] * (1.0 - fraction) + normalized[right] * fraction
        )
    return tuple(output)


def encode_pcm16_le(samples: Sequence[float]) -> bytes:
    pcm = array(
        "h",
        (
            int(round(max(-1.0, min(1.0, float(sample))) * 32_767))
            for sample in samples
        ),
    )
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def _decode_result(raw_result: object) -> Mapping[str, object]:
    if not isinstance(raw_result, str):
        raise SpeechTranscriptionError("Vosk final result must be a JSON string")
    try:
        decoded = json.loads(raw_result)
    except json.JSONDecodeError as exc:
        raise SpeechTranscriptionError("Vosk final result was invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise SpeechTranscriptionError("Vosk final result must be a JSON object")
    return decoded


def _result_text_and_words(
    result: Mapping[str, object],
) -> tuple[str, tuple[TranscriptWord, ...]]:
    selected = result
    alternatives = result.get("alternatives")
    if isinstance(alternatives, list) and alternatives:
        first = alternatives[0]
        if isinstance(first, Mapping):
            selected = first

    text_value = selected.get("text", "")
    if not isinstance(text_value, str):
        raise SpeechTranscriptionError("Vosk result text must be a string")
    text = " ".join(text_value.split())

    raw_words = selected.get("result", ())
    if raw_words is None:
        raw_words = ()
    if not isinstance(raw_words, (list, tuple)):
        raise SpeechTranscriptionError("Vosk word result must be a list")

    words: list[TranscriptWord] = []
    for index, raw_word in enumerate(raw_words):
        if not isinstance(raw_word, Mapping):
            raise SpeechTranscriptionError(
                f"Vosk word result {index} must be an object"
            )
        try:
            word_text = str(raw_word["word"]).strip()
            start = float(raw_word["start"])
            end = float(raw_word["end"])
            confidence = float(raw_word.get("conf", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechTranscriptionError(
                f"Vosk word result {index} is malformed"
            ) from exc
        words.append(TranscriptWord(word_text, start, end, confidence))
    return text, tuple(words)
