from copy import deepcopy
import sqlite3

import pytest

from velvet_audio_studio.runtime.event_protocol import EventProtocolEnvelope
from velvet_audio_studio.runtime.speech_expression_ingress import (
    SpeechDeliveryState,
    SpeechExpressionIngressError,
    SpeechExpressionIngressHandler,
    SqliteSpeechDeliveryLedger,
)
from velvet_audio_studio.voice.expression_event import (
    SPEECH_EXPRESSION_CONTRACT,
    SPEECH_EXPRESSION_EVENT,
    SpeechExpressionEventError,
)


def _speech_event(*, expression_id="response-1", text="Mister, systems nominal."):
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
            "expression_id": expression_id,
            "text": text,
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


def _envelope(event=None, *, sequence=1):
    nested = _speech_event() if event is None else event
    return EventProtocolEnvelope(
        event_type=SPEECH_EXPRESSION_EVENT,
        source_id="velvet-runtime.speech-egress",
        sequence=sequence,
        occurred_at_monotonic_ns=123_456 + sequence,
        payload={"speech_expression": nested},
    )


class RecordingOutput:
    def __init__(self, failure=None):
        self.requests = []
        self.failure = failure

    def speak(self, request):
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return object()


def test_valid_expression_is_spoken_once_with_stable_dispatch_identity(tmp_path):
    output = RecordingOutput()
    ledger = SqliteSpeechDeliveryLedger(tmp_path / "speech.sqlite3")
    handler = SpeechExpressionIngressHandler(output, ledger)

    receipt = handler.dispatch(
        _envelope(),
        dispatch_id="runtime-dispatch-1",
        ingress_receipt_id="ingress-1",
    )

    assert receipt.startswith("audio-speech-delivery-")
    assert len(output.requests) == 1
    assert output.requests[0].request_id == "runtime-dispatch-1"
    assert output.requests[0].expression_id == "response-1"
    assert ledger.state("response-1") is SpeechDeliveryState.COMPLETED


def test_duplicate_expression_is_acknowledged_without_speaking_twice(tmp_path):
    output = RecordingOutput()
    ledger = SqliteSpeechDeliveryLedger(tmp_path / "speech.sqlite3")
    handler = SpeechExpressionIngressHandler(output, ledger)

    first = handler.dispatch(
        _envelope(sequence=1),
        dispatch_id="runtime-dispatch-1",
        ingress_receipt_id="ingress-1",
    )
    second = handler.dispatch(
        _envelope(sequence=2),
        dispatch_id="runtime-dispatch-2",
        ingress_receipt_id="ingress-2",
    )

    assert first == second
    assert len(output.requests) == 1
    assert ledger.state("response-1") is SpeechDeliveryState.COMPLETED


def test_expression_identity_cannot_be_reused_with_changed_content(tmp_path):
    output = RecordingOutput()
    ledger = SqliteSpeechDeliveryLedger(tmp_path / "speech.sqlite3")
    handler = SpeechExpressionIngressHandler(output, ledger)

    handler.dispatch(
        _envelope(_speech_event(text="First sentence.")),
        dispatch_id="runtime-dispatch-1",
        ingress_receipt_id="ingress-1",
    )

    with pytest.raises(SpeechExpressionIngressError, match="different speech content"):
        handler.dispatch(
            _envelope(_speech_event(text="Changed sentence."), sequence=2),
            dispatch_id="runtime-dispatch-2",
            ingress_receipt_id="ingress-2",
        )

    assert len(output.requests) == 1


def test_output_failure_becomes_uncertain_and_is_not_automatically_replayed(tmp_path):
    output = RecordingOutput(RuntimeError("speaker edge failed"))
    ledger = SqliteSpeechDeliveryLedger(tmp_path / "speech.sqlite3")
    handler = SpeechExpressionIngressHandler(output, ledger)

    first = handler.dispatch(
        _envelope(sequence=1),
        dispatch_id="runtime-dispatch-1",
        ingress_receipt_id="ingress-1",
    )
    assert ledger.state("response-1") is SpeechDeliveryState.UNCERTAIN

    output.failure = None
    second = handler.dispatch(
        _envelope(sequence=2),
        dispatch_id="runtime-dispatch-2",
        ingress_receipt_id="ingress-2",
    )

    assert first == second
    assert len(output.requests) == 1
    assert ledger.state("response-1") is SpeechDeliveryState.UNCERTAIN


def test_recovered_started_attempt_becomes_uncertain_without_second_speech(tmp_path):
    output = RecordingOutput(KeyboardInterrupt())
    ledger = SqliteSpeechDeliveryLedger(tmp_path / "speech.sqlite3")
    handler = SpeechExpressionIngressHandler(output, ledger)

    with pytest.raises(KeyboardInterrupt):
        handler.dispatch(
            _envelope(sequence=1),
            dispatch_id="runtime-dispatch-1",
            ingress_receipt_id="ingress-1",
        )
    assert ledger.state("response-1") is SpeechDeliveryState.STARTED

    output.failure = None
    receipt = handler.dispatch(
        _envelope(sequence=2),
        dispatch_id="runtime-dispatch-2",
        ingress_receipt_id="ingress-2",
    )

    assert receipt.startswith("audio-speech-delivery-")
    assert len(output.requests) == 1
    assert ledger.state("response-1") is SpeechDeliveryState.UNCERTAIN


def test_outer_transport_must_contain_only_one_speech_expression(tmp_path):
    output = RecordingOutput()
    ledger = SqliteSpeechDeliveryLedger(tmp_path / "speech.sqlite3")
    handler = SpeechExpressionIngressHandler(output, ledger)
    envelope = _envelope()
    malformed = EventProtocolEnvelope(
        event_type=envelope.event_type,
        source_id=envelope.source_id,
        sequence=envelope.sequence,
        occurred_at_monotonic_ns=envelope.occurred_at_monotonic_ns,
        payload={
            "speech_expression": _speech_event(),
            "hardware_target": "speaker-4",
        },
    )

    with pytest.raises(SpeechExpressionIngressError, match="only speech_expression"):
        handler.dispatch(
            malformed,
            dispatch_id="runtime-dispatch-1",
            ingress_receipt_id="ingress-1",
        )
    assert output.requests == []


def test_nested_speech_is_revalidated_against_existing_authority_boundary(tmp_path):
    output = RecordingOutput()
    ledger = SqliteSpeechDeliveryLedger(tmp_path / "speech.sqlite3")
    handler = SpeechExpressionIngressHandler(output, ledger)
    event = deepcopy(_speech_event())
    event["payload"]["capability_token"] = "forbidden"

    with pytest.raises(SpeechExpressionEventError, match="forbidden"):
        handler.dispatch(
            _envelope(event),
            dispatch_id="runtime-dispatch-1",
            ingress_receipt_id="ingress-1",
        )
    assert output.requests == []


def test_private_delivery_ledger_does_not_store_spoken_text(tmp_path):
    phrase = "A deliberately private spoken sentence."
    output = RecordingOutput()
    database = tmp_path / "speech.sqlite3"
    ledger = SqliteSpeechDeliveryLedger(database)
    handler = SpeechExpressionIngressHandler(output, ledger)

    handler.dispatch(
        _envelope(_speech_event(text=phrase)),
        dispatch_id="runtime-dispatch-1",
        ingress_receipt_id="ingress-1",
    )

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT * FROM speech_expression_delivery"
        ).fetchall()
    assert rows
    assert phrase not in repr(rows)
