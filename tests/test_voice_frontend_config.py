from pathlib import Path

import pytest

from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import load_audio_service_config
from velvet_audio_studio.voice.config import (
    VoiceFrontEndConfigError,
    load_voice_frontend_settings,
)


def write_config(tmp_path: Path, voice_section: str = "") -> Path:
    path = tmp_path / "studio.yaml"
    path.write_text(
        f"""studio:
  node_id: voice-config-test
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
  max_pending_runtime_events: 32
  backlog_warning_ratio: 0.75
  backlog_max_age_ms: 30000
{voice_section}
network:
  transport: ethernet
  event_protocol_transport: stdout
  runtime_endpoint: null
""",
        encoding="utf-8",
    )
    return path


def test_voice_frontend_defaults_are_conservative(tmp_path: Path) -> None:
    settings = load_voice_frontend_settings(write_config(tmp_path))

    assert settings.enabled is True
    assert settings.frontend.vad.activation_rms == 0.03
    assert settings.frontend.vad.deactivation_rms == 0.015
    assert settings.frontend.vad.activation_packets == 2
    assert settings.frontend.vad.release_packets == 3
    assert settings.frontend.utterance.pre_roll_ms == 200
    assert settings.frontend.utterance.maximum_duration_ms == 12_000


def test_voice_frontend_can_be_disabled_without_removing_pipeline(tmp_path: Path) -> None:
    path = write_config(tmp_path, "voice_frontend:\n  enabled: false\n")
    config = load_audio_service_config(path)
    assembly = build_audio_service(config, InMemoryRuntimePublisher())

    assert assembly.voice_frontend_settings.enabled is False
    assert assembly.voice_frontend is None
    assert assembly.pipeline.voice_frontend is None
    assert assembly.describe()["voice_frontend_enabled"] is False


def test_unknown_voice_frontend_key_fails_closed(tmp_path: Path) -> None:
    path = write_config(tmp_path, "voice_frontend:\n  mystery_threshold: 0.2\n")

    with pytest.raises(VoiceFrontEndConfigError, match="unknown keys"):
        load_voice_frontend_settings(path)


def test_invalid_hysteresis_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "voice_frontend:\n"
        "  activation_rms: 0.01\n"
        "  deactivation_rms: 0.02\n",
    )

    with pytest.raises(VoiceFrontEndConfigError, match="cannot exceed"):
        load_voice_frontend_settings(path)


def test_minimum_utterance_cannot_exceed_maximum(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "voice_frontend:\n"
        "  minimum_utterance_ms: 5000\n"
        "  maximum_utterance_ms: 1000\n",
    )

    with pytest.raises(VoiceFrontEndConfigError, match="cannot exceed"):
        load_voice_frontend_settings(path)
