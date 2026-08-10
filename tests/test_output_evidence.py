from __future__ import annotations

from velvet_audio_studio.contracts import AudioPriority
from velvet_audio_studio.runtime.output_evidence import (
    AUDIO_OUTPUT_BOOKED,
    AUDIO_OUTPUT_FAILED,
    AudioOutputEvidenceEmitter,
)


def test_emitter_builds_privacy_bounded_authority_free_event() -> None:
    batches = []
    emitter = AudioOutputEvidenceEmitter(
        node_id="audio-01",
        publish_events=lambda events: batches.append(tuple(events)),
        clock_ns=lambda: 123,
        id_factory=lambda: "evidence-1",
    )

    event = emitter.booked(
        request_id="request-1",
        priority=AudioPriority.VELVET_VOICE,
        output_channels=(4,),
        expression_id="expression-1",
        profile_id="owner_default",
        model_id="velvet",
    )

    assert event.event == AUDIO_OUTPUT_BOOKED
    assert event.packet_sequence == 1
    assert event.occurred_at_monotonic_ns == 123
    assert event.payload["output_event_id"] == "evidence-1"
    assert event.payload["authority"] == "none"
    assert event.payload["evidence_only"] is True
    assert event.payload["grants_execution"] is False
    assert "text" not in event.payload
    assert "pcm_bytes" not in event.payload
    assert batches == [(event,)]


def test_failure_event_does_not_copy_exception_message_or_spoken_text() -> None:
    events = []
    emitter = AudioOutputEvidenceEmitter(
        node_id="audio-01",
        publish_events=lambda batch: events.extend(batch),
        id_factory=lambda: "failure-1",
    )

    event = emitter.failed(
        request_id="request-2",
        priority=AudioPriority.VELVET_VOICE,
        output_channels=(),
        expression_id="expression-2",
        profile_id="owner_default",
        model_id=None,
        failure_stage="synthesis",
        error=RuntimeError("Mister, this private sentence leaked into an exception"),
    )

    assert event.event == AUDIO_OUTPUT_FAILED
    assert event.payload["error_class"] == "RuntimeError"
    assert event.payload["reason"] == "synthesis failed: RuntimeError"
    assert "private sentence" not in repr(event.payload)
    assert "text" not in event.payload


def test_evidence_publish_failure_is_observable_but_does_not_raise_into_audio() -> None:
    def fail(_events):
        raise OSError("runtime unavailable")

    emitter = AudioOutputEvidenceEmitter(
        node_id="audio-01",
        publish_events=fail,
        id_factory=lambda: "evidence-failed-publish",
    )

    event = emitter.booked(
        request_id="request-3",
        priority=AudioPriority.SAFETY,
        output_channels=(4, 6),
        expression_id=None,
        profile_id="emergency",
        model_id="velvet",
    )

    assert event.payload["output_event_id"] == "evidence-failed-publish"
    assert len(emitter.publish_failures) == 1
    assert "OSError" in emitter.publish_failures[0].reason
