from __future__ import annotations

from pathlib import Path

import pytest

from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.runtime.service_runner import ReliableAudioServiceRunner
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import load_audio_service_config
from velvet_audio_studio.voice.transcribing_service_runner import (
    TranscribingAudioServiceRunner,
)
from velvet_audio_studio.voice.transcription import SpeechTranscript
from velvet_audio_studio.voice.transcription_config import (
    TranscriptionServiceConfigError,
    load_transcription_settings,
)
from velvet_audio_studio.voice.utterance import VoiceUtterance
from velvet_audio_studio.voice.vosk_transcriber import VoskTranscriberConfig


class FakeTranscriber:
    def open(self) -> None:
        return None

    def transcribe(self, utterance: VoiceUtterance) -> SpeechTranscript:
        return SpeechTranscript(
            utterance_id=utterance.utterance_id,
            text="velvet status",
            words=(),
            confidence=0.0,
            model_id="fake",
            language="en-us",
            recognizer_sample_rate_hz=16_000,
            source_duration_ms=utterance.duration_ms,
        )

    def close(self) -> None:
        return None


def _write_config(
    tmp_path: Path,
    *,
    transcription_enabled: bool,
    voice_enabled: bool = True,
    model_path: Path | None = None,
    extra_transcription: str = "",
) -> Path:
    path = tmp_path / "studio.yaml"
    model = model_path or (tmp_path / "missing-model")
    path.write_text(
        f"""studio:
  node_id: velvet-audio-test
  host_adapter: raspberry_pi_3
  hardware_adapter: audio_injector_octo
  input_channels: 6
  output_channels: 8

capture:
  source: simulated
  identity_terms: []
  sample_rate_hz: 48000
  sample_format: S32_LE
  period_frames: 480
  heartbeat_interval_ms: 5000
  idle_poll_seconds: 0
  retry_journal: state/retry.jsonl
  max_pending_runtime_events: 32
  backlog_warning_ratio: 0.75
  backlog_max_age_ms: 30000

voice_frontend:
  enabled: {str(voice_enabled).lower()}

transcription:
  enabled: {str(transcription_enabled).lower()}
  engine: vosk
  model_path: {model}
  recognizer_sample_rate_hz: 16000
  queue_capacity: 3
  worker_stop_timeout_seconds: 2.0
  wake_names:
    - hey velvet
    - velvet
    - princess
{extra_transcription}

network:
  transport: ethernet
  event_protocol_transport: stdout
  runtime_endpoint: null
""",
        encoding="utf-8",
    )
    return path


def test_disabled_transcription_needs_no_model_and_keeps_base_runner(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, transcription_enabled=False)
    config = load_audio_service_config(config_path)

    settings = load_transcription_settings(config_path)
    assembly = build_audio_service(config, InMemoryRuntimePublisher())

    assert settings.enabled is False
    assert settings.vosk is None
    assert assembly.transcription_worker is None
    assert isinstance(assembly.runner, ReliableAudioServiceRunner)
    assert assembly.describe()["transcription_enabled"] is False


def test_enabled_transcription_builds_worker_from_local_model(tmp_path: Path) -> None:
    model_path = tmp_path / "vosk-model-small-en-us-0.15"
    model_path.mkdir()
    config_path = _write_config(
        tmp_path,
        transcription_enabled=True,
        model_path=model_path,
    )
    config = load_audio_service_config(config_path)
    observed: list[VoskTranscriberConfig] = []

    def factory(vosk_config: VoskTranscriberConfig) -> FakeTranscriber:
        observed.append(vosk_config)
        return FakeTranscriber()

    assembly = build_audio_service(
        config,
        InMemoryRuntimePublisher(),
        transcriber_factory=factory,
    )

    assert isinstance(assembly.runner, TranscribingAudioServiceRunner)
    assert assembly.transcription_worker is not None
    assert observed[0].model_path == model_path.resolve()
    assert observed[0].recognizer_sample_rate_hz == 16_000
    assert assembly.describe()["transcription_model_id"] == model_path.name
    assert assembly.describe()["transcription_wake_names"] == (
        "hey velvet",
        "princess",
        "velvet",
    )


def test_enabled_transcription_requires_existing_model_directory(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, transcription_enabled=True)

    with pytest.raises(TranscriptionServiceConfigError, match="not a directory"):
        load_transcription_settings(config_path)


def test_unknown_transcription_key_fails_closed(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        transcription_enabled=False,
        extra_transcription="  mystery_switch: true",
    )

    with pytest.raises(TranscriptionServiceConfigError, match="unknown keys"):
        load_transcription_settings(config_path)


def test_transcription_requires_enabled_voice_frontend(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    config_path = _write_config(
        tmp_path,
        transcription_enabled=True,
        voice_enabled=False,
        model_path=model_path,
    )
    config = load_audio_service_config(config_path)

    with pytest.raises(ValueError, match="voice_frontend"):
        build_audio_service(
            config,
            InMemoryRuntimePublisher(),
            transcriber_factory=lambda _config: FakeTranscriber(),
        )
