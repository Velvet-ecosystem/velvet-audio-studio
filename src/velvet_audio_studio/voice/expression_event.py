from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from velvet_audio_studio.contracts import AudioPriority
from velvet_audio_studio.voice.delivery_profiles import DeliveryContext
from velvet_audio_studio.voice.output_service import (
    LocalSpeechOutputService,
    SpeechOutputRequest,
    SpeechOutputResult,
)

SPEECH_EXPRESSION_EVENT = "language.expression.speech_requested"
SPEECH_EXPRESSION_CONTRACT = "velvet.speech-expression.v1"
SPEECH_EXPRESSION_SCHEMA_VERSION = "1.0"

_FORBIDDEN_KEYS = {
    "alsa_device",
    "aplay_binary",
    "output_channel",
    "output_channels",
    "speaker_id",
    "speaker_slot",
    "speaker_slots",
    "voice_model",
    "model_path",
    "config_path",
    "volume",
    "gain",
    "gain_db",
    "pitch",
    "rate",
    "length_scale",
    "noise_scale",
    "noise_w_scale",
    "capability",
    "capability_token",
    "command",
    "court_token",
    "execution_token",
    "executor",
    "hardware_target",
    "authorization",
    "authorized",
    "authorized_by",
    "actuation",
    "actuate",
}


class SpeechExpressionEventError(ValueError):
    pass


class SpeechExpressionEventHandler:
    """Validated Event Protocol edge into the local speech-output service."""

    def __init__(self, output_service: LocalSpeechOutputService) -> None:
        self.output_service = output_service

    def handle(self, event: Mapping[str, Any]) -> SpeechOutputResult:
        return self.output_service.speak(speech_output_request_from_event(event))


def speech_output_request_from_event(event: Mapping[str, Any]) -> SpeechOutputRequest:
    """Validate one Language speech event and derive an Audio-owned request.

    The event may influence delivery posture, but it cannot choose physical
    channels, TTS implementation details, or authority. Audio Studio independently
    derives priority and uses its configured default output route.
    """

    if event.get("event_type") != SPEECH_EXPRESSION_EVENT:
        raise SpeechExpressionEventError("unexpected speech expression event type")

    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        raise SpeechExpressionEventError("speech expression metadata must be a mapping")
    if metadata.get("contract") != SPEECH_EXPRESSION_CONTRACT:
        raise SpeechExpressionEventError("unexpected speech expression contract")
    if metadata.get("schema_version") != SPEECH_EXPRESSION_SCHEMA_VERSION:
        raise SpeechExpressionEventError("unexpected speech expression schema version")
    if metadata.get("family") != "speech-expression":
        raise SpeechExpressionEventError("unexpected speech expression family")
    if metadata.get("authority") != "none" or metadata.get("expression_only") is not True:
        raise SpeechExpressionEventError("speech expression metadata must remain authority-free")

    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise SpeechExpressionEventError("speech expression payload must be a mapping")
    _validate_payload(payload)

    severity = _text(payload, "severity").casefold()
    emergency_context = _boolean(payload, "emergency_context")
    effective_severity = "emergency" if emergency_context else severity

    delivery = DeliveryContext(
        requested_profile_id=_text(payload, "requested_profile"),
        severity=effective_severity,
        driving_load=_text(payload, "driving_load").casefold(),
        audience=_text(payload, "audience").casefold(),
        quiet_requested=_boolean(payload, "quiet_requested"),
        social_allowed=_boolean(payload, "social_allowed"),
    )

    return SpeechOutputRequest(
        text=_normalized_text(payload.get("text")),
        delivery=delivery,
        priority=_priority_for(effective_severity),
        output_channels=(),
        requester=_event_source(event),
        purpose="speech_expression",
        speaker_id=None,
        expression_id=_text(payload, "expression_id"),
    )


def _validate_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SPEECH_EXPRESSION_SCHEMA_VERSION:
        raise SpeechExpressionEventError("speech expression payload schema mismatch")
    if payload.get("speech_approved") is not True:
        raise SpeechExpressionEventError("speech expression must be approved for speech")
    if payload.get("command_authority") is not False:
        raise SpeechExpressionEventError("speech expression cannot carry command authority")
    if payload.get("actuation_authority") is not False:
        raise SpeechExpressionEventError("speech expression cannot carry actuation authority")
    if payload.get("hardware_selected") is not False:
        raise SpeechExpressionEventError("speech expression cannot select hardware")
    if payload.get("synthesis_selected") is not False:
        raise SpeechExpressionEventError("speech expression cannot select synthesis")

    forbidden = _find_forbidden_keys(payload)
    if forbidden:
        raise SpeechExpressionEventError(
            "speech expression contains forbidden implementation or authority fields: "
            + ", ".join(sorted(forbidden))
        )

    _text(payload, "expression_id")
    text = _normalized_text(payload.get("text"))
    if len(text) > 4096:
        raise SpeechExpressionEventError("speech expression text exceeds 4096 characters")

    severity = _text(payload, "severity").casefold()
    if severity not in {"casual", "informational", "warning", "critical", "emergency"}:
        raise SpeechExpressionEventError("invalid speech expression severity")
    driving_load = _text(payload, "driving_load").casefold()
    if driving_load not in {"low", "medium", "high"}:
        raise SpeechExpressionEventError("invalid speech expression driving_load")

    for name in ("audience", "requested_profile", "generator", "policy_version"):
        _text(payload, name)
    for name in (
        "emergency_context",
        "quiet_requested",
        "social_allowed",
        "interrupt",
    ):
        _boolean(payload, name)


def _priority_for(severity: str) -> AudioPriority:
    if severity == "emergency":
        return AudioPriority.SAFETY
    if severity in {"warning", "critical"}:
        return AudioPriority.SYSTEM_ALERT
    return AudioPriority.VELVET_VOICE


def _event_source(event: Mapping[str, Any]) -> str:
    for name in ("source", "source_id"):
        value = event.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "velvet-language"


def _text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SpeechExpressionEventError(f"{name} must be a non-empty string")
    return value.strip()


def _boolean(payload: Mapping[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise SpeechExpressionEventError(f"{name} must be true or false")
    return value


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str):
        raise SpeechExpressionEventError("text must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise SpeechExpressionEventError("text must be non-empty")
    return normalized


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = str(key)
            if normalized_key in _FORBIDDEN_KEYS:
                found.add(normalized_key)
            found.update(_find_forbidden_keys(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found.update(_find_forbidden_keys(child))
    return found
