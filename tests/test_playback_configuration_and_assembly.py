from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import AudioServiceConfigError, load_audio_service_config
from velvet_audio_studio.voice.synthesis import SpeechSynthesisRequest, SynthesizedSpeech


class FakeSink:
    sample_rate_hz = 48_000
    channels = 8
    sample_format = AlsaPcmFormat.S32_LE
    period_frames = 480

    def __init__(self) -> None:
        self.closed = False

    def open(self) -> None:
        return None

    def write(self, payload: bytes) -> int:
        return len(payload) // (self.channels * self.sample_format.bytes_per_sample)

    def close(self) -> None:
        self.closed = True


class FakePlaybackResolution:
    accepted = True
    degraded_reasons: tuple[str, ...] = ()

    def __init__(self, sink: FakeSink) -> None:
        self.sink = sink
        self.config = SimpleNamespace(device="hw:CARD=fakeocto,DEV=0")

    def require_sink(self) -> FakeSink:
        return self.sink


class FakeSynthesizer:
    def __init__(self) -> None:
        self.closed = False

    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesizedSpeech:
        return SynthesizedSpeech(
            model_id="fake",
            profile_id=request.profile_id,
            sample_rate_hz=48_000,
            sample_width_bytes=2,
            channels=1,
            pcm_bytes=b"\x00\x00",
            text_char_count=len(request.text),
        )

    def close(self) -> None:
        self.closed = True


def _write_config(
    tmp_path: Path,
    *,
    playback_enabled: bool,
    tts_enabled: bool = False,
    default_output_channels: str = "[4]",
) -> Path:
    model = tmp_path / "velvet.onnx"
    voice_config = tmp_path / "velvet.onnx.json"
    if tts_enabled:
        model.write_bytes(b"model")
        voice_config.write_text("{}", encoding="utf-8")
    path = tmp_path / "studio.yaml"
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

playback:
  enabled: {str(playback_enabled).lower()}
  source: alsa_octo
  identity_terms: [audioinjector, octo]
  pcm_device: 0
  use_plughw: false
  sample_rate_hz: 48000
  sample_format: S32_LE
  period_frames: 480
  default_output_channels: {default_output_channels}

voice_frontend:
  enabled: false

transcription:
  enabled: false

tts:
  enabled: {str(tts_enabled).lower()}
  engine: piper
  model_path: {model}
  config_path: {voice_config}
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


def test_disabled_playback_requires_no_hardware_resolution(tmp_path: Path) -> None:
    path = _write_config(tmp_path, playback_enabled=False)
    config = load_audio_service_config(path)
    called = False

    def resolver(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled playback should not resolve hardware")

    assembly = build_audio_service(
        config,
        InMemoryRuntimePublisher(),
        playback_resolver=resolver,
    )

    assert called is False
    assert assembly.playback_engine is None
    assert assembly.speech_output_service is None
    assert assembly.describe()["playback_enabled"] is False


def test_enabled_playback_and_tts_assemble_full_local_speech_output_path(tmp_path: Path) -> None:
    path = _write_config(tmp_path, playback_enabled=True, tts_enabled=True)
    config = load_audio_service_config(path)
    sink = FakeSink()
    observed: list[dict[str, object]] = []
    synthesizer = FakeSynthesizer()

    def resolver(**kwargs):
        observed.append(kwargs)
        return FakePlaybackResolution(sink)

    assembly = build_audio_service(
        config,
        InMemoryRuntimePublisher(),
        playback_resolver=resolver,
        synthesizer_factory=lambda _config: synthesizer,
    )

    assert assembly.playback_engine is not None
    assert assembly.speech_output_service is not None
    assert assembly.describe()["playback_accepted"] is True
    assert assembly.describe()["playback_alsa_device"] == "hw:CARD=fakeocto,DEV=0"
    assert assembly.describe()["speech_output_ready"] is True
    assert observed[0]["sample_rate_hz"] == 48_000
    assert observed[0]["sample_format"] is AlsaPcmFormat.S32_LE
    assert observed[0]["period_frames"] == 480

    assembly.close_output()
    assert sink.closed is True
    assert synthesizer.closed is True


def test_playback_default_output_must_fit_studio_outputs(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        playback_enabled=False,
        default_output_channels="[8]",
    )

    with pytest.raises(AudioServiceConfigError, match="outside studio.output_channels"):
        load_audio_service_config(path)
