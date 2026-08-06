from __future__ import annotations

import json
from pathlib import Path

import pytest

from velvet_audio_studio.voice.transcription import SpeechTranscriptionError
from velvet_audio_studio.voice.utterance import VoiceUtterance
from velvet_audio_studio.voice.vosk_transcriber import (
    VoskOfflineTranscriber,
    VoskTranscriberConfig,
    encode_pcm16_le,
    resample_linear,
)


class FakeRecognizer:
    instances: list["FakeRecognizer"] = []
    final_result = json.dumps({
        "text": "hey velvet lights please",
        "result": [
            {"word": "hey", "start": 0.0, "end": 0.2, "conf": 0.8},
            {"word": "velvet", "start": 0.2, "end": 0.5, "conf": 0.6},
        ],
    })

    def __init__(self, model: object, rate: float, *args: str) -> None:
        self.model = model
        self.rate = rate
        self.args = args
        self.words = None
        self.alternatives = None
        self.audio = b""
        self.__class__.instances.append(self)

    def SetWords(self, value: bool) -> None:
        self.words = value

    def SetMaxAlternatives(self, value: int) -> None:
        self.alternatives = value

    def AcceptWaveform(self, audio: bytes) -> bool:
        self.audio = audio
        return True

    def FinalResult(self) -> str:
        return self.final_result


class FakeApi:
    KaldiRecognizer = FakeRecognizer

    def __init__(self) -> None:
        self.model_paths: list[str] = []
        self.log_levels: list[int] = []

    def SetLogLevel(self, value: int) -> None:
        self.log_levels.append(value)

    def Model(self, *, model_path: str) -> object:
        self.model_paths.append(model_path)
        return {"model_path": model_path}


def _utterance() -> VoiceUtterance:
    return VoiceUtterance(
        utterance_id="utterance-00000001",
        samples=(0.0, 0.25, 0.5, 0.75, -0.5, -1.0),
        sample_rate_hz=48_000,
        started_at_monotonic_ns=1_000_000_000,
        ended_at_monotonic_ns=1_100_000_000,
        selected_logical_name="driver_upper_mic",
        confidence=0.8,
        completion_reason="silence",
        truncated=False,
    )


def test_resample_and_pcm_conversion_are_deterministic() -> None:
    assert resample_linear(
        (0.0, 0.25, 0.5, 0.75, -0.5, -1.0),
        source_rate_hz=48_000,
        target_rate_hz=16_000,
    ) == (0.0, 0.75)
    pcm = encode_pcm16_le((-2.0, -1.0, 0.0, 1.0, 2.0))
    assert len(pcm) == 10
    assert int.from_bytes(pcm[0:2], "little", signed=True) == -32_767
    assert int.from_bytes(pcm[4:6], "little", signed=True) == 0
    assert int.from_bytes(pcm[-2:], "little", signed=True) == 32_767


def test_vosk_transcriber_lazily_opens_model_and_parses_words(tmp_path: Path) -> None:
    model_path = tmp_path / "vosk-model-small-en-us-0.15"
    model_path.mkdir()
    api = FakeApi()
    FakeRecognizer.instances.clear()
    transcriber = VoskOfflineTranscriber(
        VoskTranscriberConfig(
            model_path=model_path,
            recognizer_sample_rate_hz=16_000,
            include_words=True,
            max_alternatives=2,
            grammar=("hey velvet", "lights please"),
        ),
        api_loader=lambda: api,
    )

    assert transcriber.open_state is False
    transcript = transcriber.transcribe(_utterance())

    assert transcriber.open_state is True
    assert api.model_paths == [str(model_path.resolve())]
    assert api.log_levels == [-1]
    recognizer = FakeRecognizer.instances[-1]
    assert recognizer.rate == 16_000.0
    assert recognizer.words is True
    assert recognizer.alternatives == 2
    assert json.loads(recognizer.args[0]) == ["hey velvet", "lights please"]
    assert len(recognizer.audio) == 4
    assert transcript.text == "hey velvet lights please"
    assert transcript.model_id == model_path.name
    assert transcript.confidence == pytest.approx(0.7)
    assert [word.text for word in transcript.words] == ["hey", "velvet"]


def test_missing_model_fails_before_loading_native_api(tmp_path: Path) -> None:
    loaded = False

    def loader() -> object:
        nonlocal loaded
        loaded = True
        return FakeApi()

    transcriber = VoskOfflineTranscriber(
        VoskTranscriberConfig(model_path=tmp_path / "missing"),
        api_loader=loader,
    )

    with pytest.raises(SpeechTranscriptionError, match="model directory"):
        transcriber.open()
    assert loaded is False


def test_invalid_vosk_json_fails_closed(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    api = FakeApi()
    previous = FakeRecognizer.final_result
    FakeRecognizer.final_result = "not-json"
    try:
        transcriber = VoskOfflineTranscriber(
            VoskTranscriberConfig(model_path=model_path),
            api_loader=lambda: api,
        )
        with pytest.raises(SpeechTranscriptionError, match="invalid JSON"):
            transcriber.transcribe(_utterance())
    finally:
        FakeRecognizer.final_result = previous
