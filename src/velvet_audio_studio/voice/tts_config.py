from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from velvet_audio_studio.voice.delivery_profiles import delivery_profile
from velvet_audio_studio.voice.piper_synthesizer import PiperSynthesizerConfig


_ALLOWED_KEYS = frozenset({
    "enabled",
    "engine",
    "model_path",
    "config_path",
    "use_cuda",
    "default_profile",
})


class TtsServiceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TtsServiceSettings:
    enabled: bool
    engine: str
    piper: PiperSynthesizerConfig | None
    default_profile: str


def load_tts_settings(config_path: str | Path) -> TtsServiceSettings:
    path = Path(config_path).expanduser().resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TtsServiceConfigError(f"audio service config was not found: {path}") from exc
    except OSError as exc:
        raise TtsServiceConfigError(
            f"audio service config could not be read: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise TtsServiceConfigError(
            f"audio service config is invalid YAML: {exc}"
        ) from exc

    if not isinstance(raw, Mapping):
        raise TtsServiceConfigError("configuration root must be a mapping")
    section = raw.get("tts", {})
    if not isinstance(section, Mapping):
        raise TtsServiceConfigError("tts must be a mapping")
    unknown = sorted(set(section) - _ALLOWED_KEYS)
    if unknown:
        raise TtsServiceConfigError(
            "tts contains unknown keys: " + ", ".join(unknown)
        )

    enabled = _boolean(section.get("enabled", False), "tts.enabled")
    engine = _text(section.get("engine", "piper"), "tts.engine").casefold()
    if engine != "piper":
        raise TtsServiceConfigError("tts.engine must be piper")

    default_profile = _text(
        section.get("default_profile", "owner_default"),
        "tts.default_profile",
    )
    try:
        delivery_profile(default_profile)
    except ValueError as exc:
        raise TtsServiceConfigError(str(exc)) from exc

    piper_config: PiperSynthesizerConfig | None = None
    if enabled:
        model_raw = section.get("model_path")
        if model_raw is None:
            raise TtsServiceConfigError(
                "tts.model_path is required when tts is enabled"
            )
        model_path = _resolved_path(model_raw, "tts.model_path", path)
        config_raw = section.get("config_path")
        config_file = (
            Path(f"{model_path}.json")
            if config_raw is None
            else _resolved_path(config_raw, "tts.config_path", path)
        )
        if not model_path.is_file():
            raise TtsServiceConfigError(f"tts.model_path is not a file: {model_path}")
        if not config_file.is_file():
            raise TtsServiceConfigError(f"tts.config_path is not a file: {config_file}")
        try:
            piper_config = PiperSynthesizerConfig(
                model_path=model_path,
                config_path=config_file,
                use_cuda=_boolean(section.get("use_cuda", False), "tts.use_cuda"),
            )
        except (TypeError, ValueError) as exc:
            raise TtsServiceConfigError(str(exc)) from exc

    return TtsServiceSettings(
        enabled=enabled,
        engine=engine,
        piper=piper_config,
        default_profile=default_profile,
    )


def _resolved_path(value: Any, name: str, config_path: Path) -> Path:
    text = _text(value, name)
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TtsServiceConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise TtsServiceConfigError(f"{name} must be true or false")
    return value
