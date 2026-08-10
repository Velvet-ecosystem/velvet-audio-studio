from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from velvet_audio_studio.voice.delivery_profiles import select_delivery_profile
from velvet_audio_studio.voice.synthesis import (
    SpeechSynthesisError,
    SpeechSynthesisRequest,
    SynthesizedSpeech,
)


PiperApiLoader = Callable[[], tuple[Any, Any]]


@dataclass(frozen=True)
class PiperSynthesizerConfig:
    model_path: Path
    config_path: Path | None = None
    use_cuda: bool = False

    def __post_init__(self) -> None:
        model_path = Path(self.model_path).expanduser().resolve()
        config_path = self.config_path
        if config_path is None:
            config_path = Path(f"{model_path}.json")
        else:
            config_path = Path(config_path).expanduser().resolve()
        object.__setattr__(self, "model_path", model_path)
        object.__setattr__(self, "config_path", config_path)
        if not isinstance(self.use_cuda, bool):
            raise ValueError("use_cuda must be true or false")


class PiperOfflineSynthesizer:
    """Synthesize local PCM speech without owning wording, authority, or routing."""

    def __init__(
        self,
        config: PiperSynthesizerConfig,
        *,
        api_loader: PiperApiLoader | None = None,
    ) -> None:
        self.config = config
        self._api_loader = api_loader or _load_piper_api
        self._voice: Any | None = None
        self._synthesis_config_class: Any | None = None

    @property
    def open_state(self) -> bool:
        return self._voice is not None

    def open(self) -> None:
        if self._voice is not None:
            return
        if not self.config.model_path.is_file():
            raise SpeechSynthesisError(
                f"Piper voice model was not found: {self.config.model_path}"
            )
        assert self.config.config_path is not None
        if not self.config.config_path.is_file():
            raise SpeechSynthesisError(
                f"Piper voice config was not found: {self.config.config_path}"
            )
        try:
            voice_class, synthesis_config_class = self._api_loader()
            voice = voice_class.load(
                self.config.model_path,
                config_path=self.config.config_path,
                use_cuda=self.config.use_cuda,
            )
        except SpeechSynthesisError:
            raise
        except Exception as exc:
            raise SpeechSynthesisError(
                f"Piper voice could not be opened: {type(exc).__name__}: {exc}"
            ) from exc
        self._voice = voice
        self._synthesis_config_class = synthesis_config_class

    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesizedSpeech:
        if self._voice is None:
            self.open()
        assert self._voice is not None
        assert self._synthesis_config_class is not None

        profile = select_delivery_profile(request.delivery_context)
        synth_config = self._synthesis_config_class(
            speaker_id=request.speaker_id,
            length_scale=profile.length_scale,
            noise_scale=profile.noise_scale,
            noise_w_scale=profile.noise_w_scale,
            normalize_audio=profile.normalize_audio,
            volume=profile.volume,
        )
        try:
            chunks = self._voice.synthesize(request.text, syn_config=synth_config)
            sample_rate, sample_width, channels, pcm = _collect_chunks(chunks)
        except SpeechSynthesisError:
            raise
        except Exception as exc:
            raise SpeechSynthesisError(
                f"Piper synthesis failed: {type(exc).__name__}: {exc}"
            ) from exc

        return SynthesizedSpeech(
            model_id=self.config.model_path.stem,
            profile_id=profile.profile_id,
            sample_rate_hz=sample_rate,
            sample_width_bytes=sample_width,
            channels=channels,
            pcm_bytes=pcm,
            text_char_count=len(request.text),
        )

    def close(self) -> None:
        self._voice = None
        self._synthesis_config_class = None


def _load_piper_api() -> tuple[Any, Any]:
    try:
        from piper import PiperVoice, SynthesisConfig
    except ImportError as exc:
        raise SpeechSynthesisError(
            "Piper is not installed; install the velvet-audio-studio tts extra"
        ) from exc
    return PiperVoice, SynthesisConfig


def _collect_chunks(chunks: Iterable[Any]) -> tuple[int, int, int, bytes]:
    sample_rate: int | None = None
    sample_width: int | None = None
    channels: int | None = None
    audio_parts: list[bytes] = []

    for chunk in chunks:
        chunk_rate = int(chunk.sample_rate)
        chunk_width = int(chunk.sample_width)
        chunk_channels = int(chunk.sample_channels)
        chunk_audio = bytes(chunk.audio_int16_bytes)
        if chunk_rate <= 0 or chunk_width <= 0 or chunk_channels <= 0:
            raise SpeechSynthesisError("Piper returned an invalid audio format")
        if not chunk_audio:
            continue
        if sample_rate is None:
            sample_rate = chunk_rate
            sample_width = chunk_width
            channels = chunk_channels
        elif (
            chunk_rate != sample_rate
            or chunk_width != sample_width
            or chunk_channels != channels
        ):
            raise SpeechSynthesisError("Piper changed audio format between chunks")
        audio_parts.append(chunk_audio)

    if sample_rate is None or sample_width is None or channels is None or not audio_parts:
        raise SpeechSynthesisError("Piper returned no speech audio")
    return sample_rate, sample_width, channels, b"".join(audio_parts)
