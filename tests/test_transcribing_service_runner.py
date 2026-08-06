from pathlib import Path

from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.runtime.service_runner import CaptureFrame
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import load_audio_service_config
from velvet_audio_studio.voice.transcription import SpeechTranscript


class Clock:
    def __init__(self):
        self.value = 1_000_000_000

    def __call__(self):
        self.value += 10_000_000
        return self.value


class Transcriber:
    def open(self):
        pass

    def close(self):
        pass

    def transcribe(self, utterance):
        return SpeechTranscript(
            utterance.utterance_id,
            "velvet status",
            (),
            0.75,
            "fake-vosk",
            "en-us",
            16_000,
            utterance.duration_ms,
        )


def frame(level, captured_ns):
    samples = []
    for index in range(480):
        samples.extend((level if index % 2 == 0 else -level, 0, 0, 0, 0, 0))
    return CaptureFrame(tuple(samples), captured_ns, sample_rate_hz=48_000)


def test_service_transcribes_and_releases_wake_request(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    config_path = tmp_path / "studio.yaml"
    config_path.write_text(f"""studio:
  node_id: test
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
  retry_journal: retry.jsonl
  max_pending_runtime_events: 128
  backlog_warning_ratio: 0.75
  backlog_max_age_ms: 30000
voice_frontend:
  enabled: true
  activation_rms: 0.03
  deactivation_rms: 0.015
  activation_packets: 1
  release_packets: 1
  pre_roll_ms: 0
  minimum_utterance_ms: 0
  maximum_utterance_ms: 12000
transcription:
  enabled: true
  engine: vosk
  model_path: {model}
  queue_capacity: 2
  worker_stop_timeout_seconds: 2.0
  wake_names: [hey velvet, velvet, princess]
network:
  transport: ethernet
  event_protocol_transport: stdout
  runtime_endpoint: null
""", encoding="utf-8")
    publisher = InMemoryRuntimePublisher()
    clock = Clock()
    assembly = build_audio_service(
        load_audio_service_config(config_path),
        publisher,
        transcriber_factory=lambda _config: Transcriber(),
        simulated_items=(frame(0.2, 1_010_000_000), frame(0.0, 1_020_000_000)),
        clock_ns=clock,
        sleeper=lambda _seconds: None,
    )

    result = assembly.runner.run(max_iterations=2)

    assert len(result.iterations) == 2
    names = [event.event for event in publisher.events]
    for expected in (
        "audio.voice_activity.started",
        "audio.utterance.ready",
        "audio.transcription.queued",
        "audio.transcription.completed",
        "audio.wake_name.matched",
        "audio.transcription.worker_stopped",
    ):
        assert expected in names
    wake = next(event for event in publisher.events if event.event == "audio.wake_name.matched")
    assert wake.payload["request_text"] == "status"
    assert wake.payload["command_authority"] is False
    assert assembly.retry_queue.status.queue.pending_count == 0
