from pathlib import Path
from types import SimpleNamespace

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.service_config import load_audio_service_config
from velvet_audio_studio.speech_output_assembly import build_speech_output_service
from velvet_audio_studio.voice.synthesis import SpeechSynthesisRequest, SynthesizedSpeech


class FakeSink:
    sample_rate_hz = 48_000
    channels = 8
    sample_format = AlsaPcmFormat.S32_LE
    period_frames = 480

    def __init__(self):
        self.closed = False

    def open(self):
        return None

    def write(self, payload):
        return len(payload) // (self.channels * self.sample_format.bytes_per_sample)

    def close(self):
        self.closed = True


class FakePlaybackResolution:
    accepted = True
    degraded_reasons = ()

    def __init__(self, sink):
        self.sink = sink
        self.config = SimpleNamespace(device="hw:CARD=fakeocto,DEV=0")

    def require_sink(self):
        return self.sink


class FakeSynthesizer:
    def __init__(self):
        self.closed = False

    def synthesize(self, request: SpeechSynthesisRequest):
        return SynthesizedSpeech(
            model_id="fake",
            profile_id=request.profile_id,
            sample_rate_hz=48_000,
            sample_width_bytes=2,
            channels=1,
            pcm_bytes=b"\x00\x00",
            text_char_count=len(request.text),
        )

    def close(self):
        self.closed = True


def _write_config(tmp_path: Path):
    model = tmp_path / "velvet.onnx"
    model_config = tmp_path / "velvet.onnx.json"
    model.write_bytes(b"model")
    model_config.write_text("{}", encoding="utf-8")
    path = tmp_path / "studio.yaml"
    path.write_text(
        f"""studio:
  node_id: velvet-audio-test
  host_adapter: raspberry_pi_3
  hardware_adapter: audio_injector_octo
  input_channels: 6
  output_channels: 8

capture:
  source: alsa_octo
  identity_terms: [audioinjector, octo]
  pcm_device: 0
  use_plughw: false
  sample_rate_hz: 48000
  sample_format: S32_LE
  period_frames: 480
  heartbeat_interval_ms: 5000
  idle_poll_seconds: 0
  retry_journal: {tmp_path / 'output-events.jsonl'}
  max_pending_runtime_events: 32
  backlog_warning_ratio: 0.75
  backlog_max_age_ms: 30000

playback:
  enabled: true
  source: alsa_octo
  identity_terms: [audioinjector, octo]
  pcm_device: 0
  use_plughw: false
  sample_rate_hz: 48000
  sample_format: S32_LE
  period_frames: 480
  default_output_channels: [4]

voice_frontend:
  enabled: true

transcription:
  enabled: true
  engine: vosk
  model_path: {tmp_path / 'missing-vosk-model'}
  recognizer_sample_rate_hz: 16000
  queue_capacity: 4
  worker_stop_timeout_seconds: 1
  wake_names: [velvet]

tts:
  enabled: true
  engine: piper
  model_path: {model}
  config_path: {model_config}
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


def test_output_only_assembly_ignores_capture_and_transcription_hardware(tmp_path):
    config = load_audio_service_config(_write_config(tmp_path))
    sink = FakeSink()
    synthesizer = FakeSynthesizer()
    playback_calls = []

    def playback_resolver(**kwargs):
        playback_calls.append(kwargs)
        return FakePlaybackResolution(sink)

    assembly = build_speech_output_service(
        config,
        InMemoryRuntimePublisher(),
        playback_resolver=playback_resolver,
        synthesizer_factory=lambda _config: synthesizer,
    )

    assert assembly.speech_synthesizer is synthesizer
    assert assembly.playback_resolution.accepted is True
    assert len(playback_calls) == 1
    assert assembly.speech_output_service is not None
    assert assembly.retry_queue.status.queue.pending_count == 0

    assembly.close()
    assert synthesizer.closed is True
    assert sink.closed is True
