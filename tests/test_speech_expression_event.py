from copy import deepcopy

import pytest

from velvet_audio_studio.contracts import AudioPriority
from velvet_audio_studio.voice.expression_event import (
    SPEECH_EXPRESSION_CONTRACT,
    SPEECH_EXPRESSION_EVENT,
    SpeechExpressionEventError,
    speech_output_request_from_event,
)


def _event() -> dict[str, object]:
    return {
        "event_type": SPEECH_EXPRESSION_EVENT,
        "source": "velvet-language",
        "metadata": {
            "contract": SPEECH_EXPRESSION_CONTRACT,
            "schema_version": "1.0",
            "family": "speech-expression",
            "authority": "none",
            "expression_only": True,
        },
        "payload": {
            "schema_version": "1.0",
            "expression_id": "response-1",
            "text": "Mister, systems nominal.",
            "severity": "informational",
            "audience": "owner",
            "requested_profile": "owner_default",
            "driving_load": "low",
            "emergency_context": False,
            "quiet_requested": False,
            "social_allowed": False,
            "interrupt": False,
            "generator": "catalog",
            "policy_version": "0.1",
            "speech_approved": True,
            "command_authority": False,
            "actuation_authority": False,
            "hardware_selected": False,
            "synthesis_selected": False,
        },
    }


def test_converts_language_event_to_audio_owned_request_without_physical_route() -> None:
    request = speech_output_request_from_event(_event())

    assert request.text == "Mister, systems nominal."
    assert request.delivery.requested_profile_id == "owner_default"
    assert request.delivery.severity == "informational"
    assert request.priority is AudioPriority.VELVET_VOICE
    assert request.output_channels == ()
    assert request.speaker_id is None
    assert request.requester == "velvet-language"


def test_emergency_context_upgrades_audio_delivery_and_priority() -> None:
    event = _event()
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["emergency_context"] = True
    payload["requested_profile"] = "playful_social"
    payload["social_allowed"] = True

    request = speech_output_request_from_event(event)

    assert request.delivery.severity == "emergency"
    assert request.priority is AudioPriority.SAFETY
    assert request.delivery.requested_profile_id == "playful_social"


def test_warning_maps_to_system_alert_priority() -> None:
    event = _event()
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["severity"] = "warning"
    payload["requested_profile"] = "warning"

    request = speech_output_request_from_event(event)

    assert request.priority is AudioPriority.SYSTEM_ALERT
    assert request.delivery.severity == "warning"


def test_rejects_hardware_synthesis_or_authority_smuggling() -> None:
    for field, value in (
        ("output_channels", [4]),
        ("speaker_id", 1),
        ("model_path", "/tmp/voice.onnx"),
        ("gain_db", 4.0),
        ("capability_token", "nope"),
    ):
        event = deepcopy(_event())
        payload = event["payload"]
        assert isinstance(payload, dict)
        payload[field] = value
        with pytest.raises(SpeechExpressionEventError, match="forbidden"):
            speech_output_request_from_event(event)


def test_rejects_unapproved_or_authority_bearing_speech() -> None:
    event = deepcopy(_event())
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["speech_approved"] = False
    with pytest.raises(SpeechExpressionEventError, match="approved"):
        speech_output_request_from_event(event)

    event = deepcopy(_event())
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["command_authority"] = True
    with pytest.raises(SpeechExpressionEventError, match="command authority"):
        speech_output_request_from_event(event)
