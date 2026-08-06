from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from velvet_audio_studio.voice.vosk_transcriber import VoskTranscriberConfig
from velvet_audio_studio.voice.wake_gate import WakeNameConfig


_ALLOWED_KEYS = frozenset({
    "enabled",
    "engine",
    "model_path",
    "recognizer_sample_rate_hz",
    "language",
    "include_words",
    "max_alternatives",
    "log_level",
    "grammar",
    "queue_capacity",
    "worker_stop_timeout_seconds",
    "wake_names",
})


class TranscriptionServiceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TranscriptionServiceSettings:
    enabled: bool
    engine: str
    vosk: VoskTranscriberConfig | None
    wake: WakeNameConfig
    queue_capacity: int
    worker_stop_timeout_seconds: float


def load_transcription_settings(
    config_path: str | Path,
) -> TranscriptionServiceSettings:
    path = Path(config_path).expanduser().resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TranscriptionServiceConfigError(
            f"audio service config was not found: {path}"
        ) from exc
    except OSError as exc:
        raise TranscriptionServiceConfigError(
            f"audio service config could not be read: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise TranscriptionServiceConfigError(
            f"audio service config is invalid YAML: {exc}"
        ) from exc

    if not isinstance(raw, Mapping):
        raise TranscriptionServiceConfigError("configuration root must be a mapping")
    section = raw.get("transcription", {})
    if not isinstance(section, Mapping):
        raise TranscriptionServiceConfigError("transcription must be a mapping")
    unknown = sorted(set(section) - _ALLOWED_KEYS)
    if unknown:
        raise TranscriptionServiceConfigError(
            "transcription contains unknown keys: " + ", ".join(unknown)
        )

    enabled = _boolean(section.get("enabled", False), "transcription.enabled")
    engine = _text(section.get("engine", "vosk"), "transcription.engine").casefold()
    if engine != "vosk":
        raise TranscriptionServiceConfigError("transcription.engine must be vosk")

    wake_names = _string_tuple(
        section.get("wake_names", ("hey velvet", "velvet", "princess")),
        "transcription.wake_names",
    )
    queue_capacity = _positive_int(
        section.get("queue_capacity", 4),
        "transcription.queue_capacity",
    )
    stop_timeout = _positive_number(
        section.get("worker_stop_timeout_seconds", 10.0),
        "transcription.worker_stop_timeout_seconds",
    )

    model_raw = section.get("model_path")
    vosk_config: VoskTranscriberConfig | None = None
    if enabled:
        if model_raw is None:
            raise TranscriptionServiceConfigError(
                "transcription.model_path is required when transcription is enabled"
            )
        model_path = _resolved_path(model_raw, "transcription.model_path", path)
        if not model_path.is_dir():
            raise TranscriptionServiceConfigError(
                f"transcription.model_path is not a directory: {model_path}"
            )
        grammar = _string_tuple(
            section.get("grammar", ()),
            "transcription.grammar",
            allow_empty=True,
        )
        try:
            vosk_config = VoskTranscriberConfig(
                model_path=model_path,
                recognizer_sample_rate_hz=_positive_int(
                    section.get("recognizer_sample_rate_hz", 16_000),
                    "transcription.recognizer_sample_rate_hz",
                ),
                language=_text(
                    section.get("language", "en-us"),
                    "transcription.language",
                ),
                include_words=_boolean(
                    section.get("include_words", True),
                    "transcription.include_words",
                ),
                max_alternatives=_nonnegative_int(
                    section.get("max_alternatives", 0),
                    "transcription.max_alternatives",
                ),
                log_level=_integer(
                    section.get("log_level", -1),
                    "transcription.log_level",
                ),
                grammar=grammar,
            )
        except (TypeError, ValueError) as exc:
            raise TranscriptionServiceConfigError(str(exc)) from exc

    try:
        wake = WakeNameConfig(wake_names)
    except (TypeError, ValueError) as exc:
        raise TranscriptionServiceConfigError(str(exc)) from exc
    return TranscriptionServiceSettings(
        enabled=enabled,
        engine=engine,
        vosk=vosk_config,
        wake=wake,
        queue_capacity=queue_capacity,
        worker_stop_timeout_seconds=stop_timeout,
    )


def _resolved_path(value: Any, name: str, config_path: Path) -> Path:
    text = _text(value, name)
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _string_tuple(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TranscriptionServiceConfigError(f"{name} must be a list of strings")
    values = tuple(_text(item, f"{name}[{index}]") for index, item in enumerate(value))
    if not allow_empty and not values:
        raise TranscriptionServiceConfigError(f"{name} cannot be empty")
    return values


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranscriptionServiceConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise TranscriptionServiceConfigError(f"{name} must be true or false")
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TranscriptionServiceConfigError(f"{name} must be an integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    parsed = _integer(value, name)
    if parsed <= 0:
        raise TranscriptionServiceConfigError(f"{name} must be positive")
    return parsed


def _nonnegative_int(value: Any, name: str) -> int:
    parsed = _integer(value, name)
    if parsed < 0:
        raise TranscriptionServiceConfigError(f"{name} must be non-negative")
    return parsed


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise TranscriptionServiceConfigError(f"{name} must be a positive number")
    return float(value)
