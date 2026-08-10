from __future__ import annotations

from pathlib import Path

from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import load_audio_service_config
from velvet_audio_studio.voice.piper_synthesizer import PiperSynthesizerConfig
from velvet_audio_studio.voice.synthesis import SpeechSynthesisRequest, SynthesizedSpeech


class FakeSynthesizer:
    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesizedSpeech:
        return SynthesizedSpeech(
            model_id="fake",
            profile_id=request.profile_id,
            sample_rate_hz=22050,
            sample_width_bytes=2,
            channels=1,
            pcm_bytes=b"\x00\x00",
            text_char_count=len(request.text),
        )

    def close(self) -> None:
        return None


def _write_config(
    tmp_path: Path,
    *,
    tts_enabled: bool,
    model_path: Path | None = None,
) -> Path:
    path = tmp_path / "studio.yaml"
    model = model_path or (tmp_path / "missing.onnx")
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
  enabled: false

transcription:
  enabled: false

tts:
  enabled: {str(tts_enabled).lower()}
  engine: piper
  model_path: {model}
  default_profile: owner_default
  use_cuda: false

network:
  transport: ethernet
  event_protocol_transport: stdout
  runtime_endpoint: null
""",
        encoding="utf-8",
    )
    return path


def test_disabled_tts_assembles_without_synthesizer(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, tts_enabled=False)
    config = load_audio_service_config(config_path)
    assembly = build_audio_service(config, InMemoryRuntimePublisher())

    assert assembly.tts_settings.enabled is False
    assert assembly.speech_synthesizer is None
    assert assembly.describe()["tts_enabled"] is False
    assert assembly.describe()["tts_model_id"] is None


def test_enabled_tts_assembles_lazy_synthesizer_from_local_voice(tmp_path: Path) -> None:
    model = tmp_path / "velvet.onnx"
    voice_config = tmp_path / "velvet.onnx.json"
    model.write_bytes(b"model")
    voice_config.write_text("{}", encoding="utf-8")
    config_path = _write_config(tmp_path, tts_enabled=True, model_path=model)
    config = load_audio_service_config(config_path)
    observed: list[PiperSynthesizerConfig] = []
    fake = FakeSynthesizer()

    def factory(piper_config: PiperSynthesizerConfig) -> FakeSynthesizer:
        observed.append(piper_config)
        return fake

    assembly = build_audio_service(
        config,
        InMemoryRuntimePublisher(),
        synthesizer_factory=factory,
    )

    assert assembly.speech_synthesizer is fake
    assert observed[0].model_path == model.resolve()
    assert observed[0].config_path == voice_config.resolve()
    assert assembly.describe()["tts_enabled"] is True
    assert assembly.describe()["tts_engine"] == "piper"
    assert assembly.describe()["tts_model_id"] == "velvet"
    assert assembly.describe()["tts_default_profile"] == "owner_default"
