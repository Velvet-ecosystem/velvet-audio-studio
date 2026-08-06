from __future__ import annotations

from dataclasses import replace

from velvet_audio_studio.voice.speech_processor import LocalSpeechProcessor
from velvet_audio_studio.voice.transcription import SpeechTranscript, TranscriptWord
from velvet_audio_studio.voice.utterance import VoiceUtterance
from velvet_audio_studio.voice.wake_gate import WakeNameConfig, WakeNameGate


class FakeTranscriber:
    def __init__(self, transcript: SpeechTranscript | Exception) -> None:
        self.transcript = transcript
        self.open_count = 0
        self.close_count = 0

    def open(self) -> None:
        self.open_count += 1

    def transcribe(self, utterance: VoiceUtterance) -> SpeechTranscript:
        if isinstance(self.transcript, Exception):
            raise self.transcript
        return replace(self.transcript, utterance_id=utterance.utterance_id)

    def close(self) -> None:
        self.close_count += 1


def _transcript(text: str) -> SpeechTranscript:
    return SpeechTranscript(
        utterance_id="placeholder",
        text=text,
        words=(TranscriptWord("word", 0.0, 0.2, 0.8),),
        confidence=0.8,
        model_id="small-en-us",
        language="en-us",
        recognizer_sample_rate_hz=16_000,
        source_duration_ms=500,
    )


def _utterance() -> VoiceUtterance:
    return VoiceUtterance(
        utterance_id="utterance-00000007",
        samples=(0.1,) * 32,
        sample_rate_hz=16_000,
        started_at_monotonic_ns=1_000,
        ended_at_monotonic_ns=2_000,
        selected_logical_name="driver_upper_mic",
        confidence=0.7,
        completion_reason="silence",
        truncated=False,
    )


def test_wake_gate_prefers_longest_prefix_and_normalizes_punctuation() -> None:
    gate = WakeNameGate(WakeNameConfig(("velvet", "hey velvet", "princess")))

    decision = gate.evaluate(_transcript("HEY, VELVET... turn the music down"))

    assert decision.matched is True
    assert decision.wake_name == "hey velvet"
    assert decision.request_text == "turn the music down"


def test_wake_name_mentioned_later_does_not_open_gate() -> None:
    gate = WakeNameGate()

    decision = gate.evaluate(_transcript("tell velvet the cabin is warm"))

    assert decision.matched is False
    assert decision.request_text == ""


def test_princess_is_a_valid_wake_name() -> None:
    decision = WakeNameGate().evaluate(_transcript("Princess open diagnostics"))

    assert decision.matched is True
    assert decision.wake_name == "princess"
    assert decision.request_text == "open diagnostics"


def test_processor_releases_only_wake_addressed_request_text() -> None:
    processor = LocalSpeechProcessor(FakeTranscriber(_transcript("Velvet show diagnostics")))

    result = processor.process(
        _utterance(),
        occurred_at_monotonic_ns=3_000,
        packet_sequence=9,
    )

    assert [event.event for event in result.events] == [
        "audio.transcription.completed",
        "audio.wake_name.matched",
    ]
    completion, wake = result.events
    assert completion.payload["transcript_text_in_event"] is False
    assert "text" not in completion.payload
    assert wake.payload["request_text"] == "show diagnostics"
    assert wake.payload["command_authority"] is False
    assert wake.payload["full_transcript_in_event"] is False
    assert all("samples" not in event.payload for event in result.events)


def test_unmatched_transcript_text_remains_local() -> None:
    processor = LocalSpeechProcessor(FakeTranscriber(_transcript("the road is noisy")))

    result = processor.process(
        _utterance(),
        occurred_at_monotonic_ns=3_000,
        packet_sequence=9,
    )

    assert result.transcript is not None
    assert result.transcript.text == "the road is noisy"
    event = result.events[-1]
    assert event.event == "audio.wake_name.not_matched"
    assert event.payload["transcript_text_in_event"] is False
    assert "the road is noisy" not in repr(event.payload)


def test_transcription_failure_becomes_metadata_event() -> None:
    processor = LocalSpeechProcessor(FakeTranscriber(RuntimeError("decoder unavailable")))

    result = processor.process(
        _utterance(),
        occurred_at_monotonic_ns=3_000,
        packet_sequence=9,
    )

    assert result.transcript is None
    assert result.error == "RuntimeError: decoder unavailable"
    assert result.events[0].event == "audio.transcription.failed"
    assert result.events[0].payload["raw_samples_in_event"] is False
