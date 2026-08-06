from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from velvet_audio_studio.voice.front_end import LocalVoiceFrontEndConfig
from velvet_audio_studio.voice.utterance import UtteranceCaptureConfig
from velvet_audio_studio.voice.vad import VoiceActivityConfig


_ALLOWED_KEYS = frozenset({
    "enabled",
    "activation_rms",
    "deactivation_rms",
    "activation_packets",
    "release_packets",
    "pre_roll_ms",
    "minimum_utterance_ms",
    "maximum_utterance_ms",
})


class VoiceFrontEndConfigError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceFrontEndServiceSettings:
    enabled: bool
    frontend: LocalVoiceFrontEndConfig


def load_voice_frontend_settings(
    config_path: str | Path,
) -> VoiceFrontEndServiceSettings:
    path = Path(config_path).expanduser().resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VoiceFrontEndConfigError(
            f"audio service config was not found: {path}"
        ) from exc
    except OSError as exc:
        raise VoiceFrontEndConfigError(
            f"audio service config could not be read: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise VoiceFrontEndConfigError(
            f"audio service config is invalid YAML: {exc}"
        ) from exc

    if not isinstance(raw, Mapping):
        raise VoiceFrontEndConfigError("configuration root must be a mapping")
    section = raw.get("voice_frontend", {})
    if not isinstance(section, Mapping):
        raise VoiceFrontEndConfigError("voice_frontend must be a mapping")

    unknown = sorted(set(section) - _ALLOWED_KEYS)
    if unknown:
        raise VoiceFrontEndConfigError(
            "voice_frontend contains unknown keys: " + ", ".join(unknown)
        )

    enabled = _boolean(section.get("enabled", True), "voice_frontend.enabled")
    try:
        vad = VoiceActivityConfig(
            activation_rms=_nonnegative_number(
                section.get("activation_rms", 0.03),
                "voice_frontend.activation_rms",
            ),
            deactivation_rms=_nonnegative_number(
                section.get("deactivation_rms", 0.015),
                "voice_frontend.deactivation_rms",
            ),
            activation_packets=_positive_int(
                section.get("activation_packets", 2),
                "voice_frontend.activation_packets",
            ),
            release_packets=_positive_int(
                section.get("release_packets", 3),
                "voice_frontend.release_packets",
            ),
        )
        utterance = UtteranceCaptureConfig(
            pre_roll_ms=_nonnegative_int(
                section.get("pre_roll_ms", 200),
                "voice_frontend.pre_roll_ms",
            ),
            minimum_duration_ms=_nonnegative_int(
                section.get("minimum_utterance_ms", 120),
                "voice_frontend.minimum_utterance_ms",
            ),
            maximum_duration_ms=_positive_int(
                section.get("maximum_utterance_ms", 12_000),
                "voice_frontend.maximum_utterance_ms",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise VoiceFrontEndConfigError(str(exc)) from exc

    return VoiceFrontEndServiceSettings(
        enabled=enabled,
        frontend=LocalVoiceFrontEndConfig(vad=vad, utterance=utterance),
    )


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise VoiceFrontEndConfigError(f"{name} must be true or false")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise VoiceFrontEndConfigError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VoiceFrontEndConfigError(f"{name} must be a non-negative integer")
    return value


def _nonnegative_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise VoiceFrontEndConfigError(f"{name} must be a non-negative number")
    return float(value)
