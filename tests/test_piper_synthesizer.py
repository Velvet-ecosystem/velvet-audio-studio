from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from velvet_audio_studio.voice.delivery_profiles import (
    delivery_profile,
    delivery_profile_ids,
)
from velvet_audio_studio.voice.piper_synthesizer import (
    PiperOfflineSynthesizer,
    PiperSynthesizerConfig,
)
from velvet_audio_studio.voice.synthesis import (
    MAX_TTS_TEXT_CHARS,
    SpeechSynthesisError,
    SpeechSynthesisRequest,
)


@dataclass
class FakeChunk:
    sample_rate: int = 22050
    sample_width: int = 2
    sample_channels: int = 1
    audio_int16_bytes: bytes = b"\x01\x00\x02\x00"


class FakeSynthesisConfig:
    def __init__(self, **kwargs: object) -> None:
        self.values = kwargs


class FakeVoice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, FakeSynthesisConfig]] = []

    def synthesize(self, text: str, *, syn_config: FakeSynthesisConfig):
        self.calls.append((text, syn_config))
        return (FakeChunk(), FakeChunk(audio_int16_bytes=b"\x03\x00"))


class FakePiperVoice:
    loaded: list[tuple[Path, Path, bool]] = []
    voice = FakeVoice()

    @classmethod
    def load(
        cls,
        model_path: Path,
        *,
        config_path: Path,
        use_cuda: bool,
    ) -> FakeVoice:
        cls.loaded.append((model_path, config_path, use_cuda))
        return cls.voice


def _voice_files(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "velvet.onnx"
    config = tmp_path / "velvet.onnx.json"
    model.write_bytes(b"model")
    config.write_text("{}", encoding="utf-8")
    return model, config


def _synthesizer(tmp_path: Path) -> PiperOfflineSynthesizer:
    model, config = _voice_files(tmp_path)
    FakePiperVoice.loaded.clear()
    FakePiperVoice.voice = FakeVoice()
    return PiperOfflineSynthesizer(
        PiperSynthesizerConfig(model_path=model, config_path=config),
        api_loader=lambda: (FakePiperVoice, FakeSynthesisConfig),
    )


def test_named_profiles_are_bounded_and_safety_profiles_are_locked() -> None:
    ids = delivery_profile_ids()
    assert "owner_default" in ids
    assert "guest_reserved" in ids
    assert "quiet_night" in ids
    assert "playful_social" in ids
    assert delivery_profile("warning").safety_locked is True
    assert delivery_profile("emergency").safety_locked is True
    assert delivery_profile("high_driving_load").safety_locked is True
    assert delivery_profile("owner_default").safety_locked is False


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown delivery profile"):
        delivery_profile("invented_by_caller")


def test_request_normalizes_text_and_bounds_resource_use() -> None:
    request = SpeechSynthesisRequest("  Mister,   systems nominal.  ")
    assert request.text == "Mister, systems nominal."
    with pytest.raises(ValueError, match="exceeds"):
        SpeechSynthesisRequest("x" * (MAX_TTS_TEXT_CHARS + 1))


def test_piper_synthesizer_lazily_loads_and_applies_named_profile(tmp_path: Path) -> None:
    synthesizer = _synthesizer(tmp_path)
    model = synthesizer.config.model_path
    config = synthesizer.config.config_path

    assert synthesizer.open_state is False
    result = synthesizer.synthesize(
        SpeechSynthesisRequest(
            "Mister, CAN has disconnected.",
            profile_id="warning",
        )
    )

    assert synthesizer.open_state is True
    assert config is not None
    assert FakePiperVoice.loaded == [(model, config, False)]
    assert result.model_id == "velvet"
    assert result.profile_id == "warning"
    assert result.sample_rate_hz == 22050
    assert result.sample_width_bytes == 2
    assert result.channels == 1
    assert result.pcm_bytes == b"\x01\x00\x02\x00\x03\x00"
    assert result.frame_count == 3
    assert result.duration_ms == pytest.approx(3 * 1000 / 22050)

    _, synth_config = FakePiperVoice.voice.calls[-1]
    profile = delivery_profile("warning")
    assert synth_config.values["length_scale"] == profile.length_scale
    assert synth_config.values["volume"] == profile.volume
    assert synth_config.values["noise_scale"] == profile.noise_scale
    assert synth_config.values["noise_w_scale"] == profile.noise_w_scale


def test_emergency_context_cannot_be_downgraded_to_playful_style(tmp_path: Path) -> None:
    synthesizer = _synthesizer(tmp_path)
    result = synthesizer.synthesize(
        SpeechSynthesisRequest(
            "Critical system unavailable.",
            profile_id="playful_social",
            severity="emergency",
            social_allowed=True,
        )
    )
    assert result.profile_id == "emergency"
    _, synth_config = FakePiperVoice.voice.calls[-1]
    emergency = delivery_profile("emergency")
    assert synth_config.values["length_scale"] == emergency.length_scale
    assert synth_config.values["noise_scale"] == emergency.noise_scale


def test_playful_profile_requires_explicit_social_permission(tmp_path: Path) -> None:
    synthesizer = _synthesizer(tmp_path)
    blocked = synthesizer.synthesize(
        SpeechSynthesisRequest(
            "That worked.",
            profile_id="playful_social",
            social_allowed=False,
        )
    )
    assert blocked.profile_id == "owner_default"


def test_missing_voice_files_fail_before_importing_piper(tmp_path: Path) -> None:
    loaded = False

    def loader():
        nonlocal loaded
        loaded = True
        return FakePiperVoice, FakeSynthesisConfig

    synthesizer = PiperOfflineSynthesizer(
        PiperSynthesizerConfig(model_path=tmp_path / "missing.onnx"),
        api_loader=loader,
    )
    with pytest.raises(SpeechSynthesisError, match="voice model"):
        synthesizer.open()
    assert loaded is False


def test_piper_rejects_audio_format_change_between_chunks(tmp_path: Path) -> None:
    model, config = _voice_files(tmp_path)

    class ChangingVoice(FakeVoice):
        def synthesize(self, text: str, *, syn_config: FakeSynthesisConfig):
            return (
                FakeChunk(),
                FakeChunk(sample_rate=24000),
            )

    class ChangingPiperVoice(FakePiperVoice):
        voice = ChangingVoice()

    synthesizer = PiperOfflineSynthesizer(
        PiperSynthesizerConfig(model_path=model, config_path=config),
        api_loader=lambda: (ChangingPiperVoice, FakeSynthesisConfig),
    )
    with pytest.raises(SpeechSynthesisError, match="changed audio format"):
        synthesizer.synthesize(SpeechSynthesisRequest("test"))
