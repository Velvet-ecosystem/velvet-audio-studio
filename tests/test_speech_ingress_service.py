import json

from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    encode_event_protocol_envelope,
    event_protocol_idempotency_key,
)
from velvet_audio_studio.runtime.ingress_dispatch import DispatchCycleState
from velvet_audio_studio.runtime.speech_expression_ingress import SpeechDeliveryState
from velvet_audio_studio.runtime.speech_ingress_service import (
    build_speech_ingress_components,
)
from velvet_audio_studio.voice.expression_event import (
    SPEECH_EXPRESSION_CONTRACT,
    SPEECH_EXPRESSION_EVENT,
)


def _speech_event():
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
            "expression_id": "response-service-1",
            "text": "Mister, the speech route is alive.",
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


def _envelope():
    return EventProtocolEnvelope(
        event_type=SPEECH_EXPRESSION_EVENT,
        source_id="velvet-runtime.speech-egress",
        sequence=7,
        occurred_at_monotonic_ns=987_654,
        payload={"speech_expression": _speech_event()},
    )


class RecordingOutput:
    def __init__(self):
        self.requests = []

    def speak(self, request):
        self.requests.append(request)
        return object()


def _post(components, envelope):
    key = event_protocol_idempotency_key(envelope)
    return components.receiver.accept(
        method="POST",
        path="/v1/speech-expressions",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": key,
            "X-Velvet-Event-ID": key,
        },
        body=encode_event_protocol_envelope(envelope),
    )


def test_http_acceptance_and_local_dispatch_remain_distinct_truths(tmp_path):
    output = RecordingOutput()
    components = build_speech_ingress_components(
        tmp_path / "speech.sqlite3",
        output,
    )
    envelope = _envelope()

    accepted = _post(components, envelope)
    accepted_body = json.loads(accepted.body)

    assert accepted.status == 202
    assert accepted_body["receipt_id"].startswith("runtime-ingress-")
    assert output.requests == []
    assert components.queue.stats().pending == 1

    dispatched = components.dispatcher.process_one()

    assert dispatched.state is DispatchCycleState.PROCESSED
    assert dispatched.downstream_receipt_id.startswith("audio-speech-delivery-")
    assert len(output.requests) == 1
    assert output.requests[0].request_id == dispatched.claim.dispatch_id
    assert components.delivery_ledger.state("response-service-1") is SpeechDeliveryState.COMPLETED
    assert components.queue.stats().processed == 1


def test_duplicate_http_delivery_does_not_create_second_acoustic_attempt(tmp_path):
    output = RecordingOutput()
    components = build_speech_ingress_components(
        tmp_path / "speech.sqlite3",
        output,
    )
    envelope = _envelope()

    first = _post(components, envelope)
    assert first.status == 202
    assert components.dispatcher.process_one().state is DispatchCycleState.PROCESSED

    duplicate = _post(components, envelope)
    duplicate_body = json.loads(duplicate.body)

    assert duplicate.status == 409
    assert duplicate_body["duplicate"] is True
    assert len(output.requests) == 1
    assert components.dispatcher.process_one().state is DispatchCycleState.IDLE


def test_receiver_rejects_transport_payload_that_exceeds_speech_limit(tmp_path):
    output = RecordingOutput()
    components = build_speech_ingress_components(
        tmp_path / "speech.sqlite3",
        output,
        max_request_bytes=128,
    )
    envelope = _envelope()

    response = _post(components, envelope)

    assert response.status == 413
    assert components.queue.stats().pending == 0
    assert output.requests == []
