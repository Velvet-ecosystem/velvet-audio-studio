from __future__ import annotations

from pathlib import Path

import pytest

from velvet_audio_studio.voice.tts_config import (
    TtsServiceConfigError,
    load_tts_settings,
)


def _write_config(path: Path, tts_yaml: str) -> None:
    path.write_text(
        "studio:\n"
        "  node_id: test\n"
        "capture:\n"
        "  source: simulated\n"
        "tts:\n"
        + tts_yaml,
        encoding="utf-8",
    )


def test_tts_defaults_to_disabled_piper(tmp_path: Path) -> None:
    config = tmp_path / "studio.yaml"
    _write_config(config, "  enabled: false\n")
    settings = load_tts_settings(config)
    assert settings.enabled is False
    assert settings.engine == "piper"
    assert settings.piper is None
    assert settings.default_profile == "owner_default"


def test_enabled_tts_requires_local_model_and_config(tmp_path: Path) -> None:
    config = tmp_path / "studio.yaml"
    _write_config(
        config,
        "  enabled: true\n"
        "  engine: piper\n"
        "  model_path: voices/velvet.onnx\n",
    )
    with pytest.raises(TtsServiceConfigError, match="model_path is not a file"):
        load_tts_settings(config)


def test_enabled_tts_resolves_local_voice_files(tmp_path: Path) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    model = voices / "velvet.onnx"
    voice_config = voices / "velvet.onnx.json"
    model.write_bytes(b"model")
    voice_config.write_text("{}", encoding="utf-8")
    config = tmp_path / "studio.yaml"
    _write_config(
        config,
        "  enabled: true\n"
        "  engine: piper\n"
        "  model_path: voices/velvet.onnx\n"
        "  default_profile: quiet_night\n"
        "  use_cuda: false\n",
    )

    settings = load_tts_settings(config)
    assert settings.enabled is True
    assert settings.piper is not None
    assert settings.piper.model_path == model.resolve()
    assert settings.piper.config_path == voice_config.resolve()
    assert settings.default_profile == "quiet_night"


def test_unknown_tts_keys_fail_closed(tmp_path: Path) -> None:
    config = tmp_path / "studio.yaml"
    _write_config(config, "  enabled: false\n  magic_emotion_knob: 99\n")
    with pytest.raises(TtsServiceConfigError, match="unknown keys"):
        load_tts_settings(config)


def test_unknown_delivery_profile_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "studio.yaml"
    _write_config(
        config,
        "  enabled: false\n"
        "  default_profile: dramatic_movie_trailer\n",
    )
    with pytest.raises(TtsServiceConfigError, match="unknown delivery profile"):
        load_tts_settings(config)
