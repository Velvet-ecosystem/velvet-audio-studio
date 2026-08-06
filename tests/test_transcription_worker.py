from __future__ import annotations

from threading import Event, current_thread
from time import monotonic, sleep

from velvet_audio_studio.voice.speech_processor import LocalSpeechProcessor
from velvet_audio_studio.voice.transcription import SpeechTranscript
from velvet_audio_studio.voice.transcription_worker import (
    BoundedTranscriptionWorker,
    TranscriptionWorkerState,
)
from velvet_audio_studio.voice.utterance import VoiceUtterance


class FakeTranscriber:
    def __init__(
        self,
        *,
        open_error: Exception | None = None,
        entered: Event | None = None,
        release: Event | None = None,
    ) -> None:
        self.open_error = open_error
        self.entered = entered
        self.release = release
        self.thread_names: list[str] = []

    def open(self) -> None:
        if self.open_error is not None:
            raise self.open_error

    def transcribe(self, utterance: VoiceUtterance) -> SpeechTranscript:
        self.thread_names.append(current_thread().name)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(2.0)
        return SpeechTranscript(
            utterance_id=utterance.utterance_id,
            text="velvet status",
            words=(),
            confidence=0.0,
            model_id="fake-model",
            language="en-us",
            recognizer_sample_rate_hz=16_000,
            source_duration_ms=utterance.duration_ms,
        )

    def close(self) -> None:
        return None


def _utterance(sequence: int = 1) -> VoiceUtterance:
    return VoiceUtterance(
        utterance_id=f"utterance-{sequence:08d}",
        samples=(0.1,) * 32,
        sample_rate_hz=16_000,
        started_at_monotonic_ns=1_000,
        ended_at_monotonic_ns=2_000,
        selected_logical_name="driver_upper_mic",
        confidence=0.8,
        completion_reason="silence",
        truncated=False,
    )


def _wait_for_state(
    worker: BoundedTranscriptionWorker,
    state: TranscriptionWorkerState,
) -> None:
    deadline = monotonic() + 2.0
    while monotonic() < deadline:
        if worker.state is state:
            return
        sleep(0.005)
    raise AssertionError(f"worker did not reach {state}")


def _wait_for_processing(worker: BoundedTranscriptionWorker):
    deadline = monotonic() + 2.0
    observed = []
    while monotonic() < deadline:
        observed.extend(worker.drain())
        for result in observed:
            if result.processing is not None:
                return result, tuple(observed)
        sleep(0.005)
    raise AssertionError("worker did not return a transcription result")


def test_worker_decodes_off_capture_thread_and_returns_wake_events() -> None:
    transcriber = FakeTranscriber()
    worker = BoundedTranscriptionWorker(
        LocalSpeechProcessor(transcriber),
        poll_seconds=0.005,
    )
    starting = worker.start(occurred_at_monotonic_ns=10, packet_sequence=0)
    _wait_for_state(worker, TranscriptionWorkerState.RUNNING)

    submission = worker.submit(
        _utterance(),
        occurred_at_monotonic_ns=20,
        packet_sequence=1,
    )
    processing, observed = _wait_for_processing(worker)
    stopped = worker.stop(timeout_seconds=2.0)

    assert starting[0].event == "audio.transcription.worker_starting"
    assert submission.accepted is True
    assert submission.events[0].event == "audio.transcription.queued"
    assert processing.processing is not None
    assert [event.event for event in processing.processing.events] == [
        "audio.transcription.completed",
        "audio.wake_name.matched",
    ]
    assert transcriber.thread_names == ["velvet-audio-transcription"]
    all_events = [event.event for result in observed + stopped for event in result.events]
    assert "audio.transcription.worker_ready" in all_events
    assert "audio.transcription.worker_stopped" in all_events
    assert worker.state is TranscriptionWorkerState.STOPPED


def test_worker_reports_full_queue_without_silent_drop() -> None:
    entered = Event()
    release = Event()
    worker = BoundedTranscriptionWorker(
        LocalSpeechProcessor(FakeTranscriber(entered=entered, release=release)),
        queue_capacity=1,
        poll_seconds=0.005,
    )
    worker.start(occurred_at_monotonic_ns=10, packet_sequence=0)
    _wait_for_state(worker, TranscriptionWorkerState.RUNNING)

    first = worker.submit(_utterance(1), occurred_at_monotonic_ns=20, packet_sequence=1)
    assert entered.wait(1.0)
    second = worker.submit(_utterance(2), occurred_at_monotonic_ns=30, packet_sequence=2)
    third = worker.submit(_utterance(3), occurred_at_monotonic_ns=40, packet_sequence=3)

    assert first.accepted is True
    assert second.accepted is True
    assert third.accepted is False
    assert third.events[0].event == "audio.transcription.queue_full"
    assert third.events[0].payload["raw_samples_in_event"] is False
    release.set()
    worker.stop(timeout_seconds=2.0)


def test_worker_open_failure_rejects_new_utterances() -> None:
    worker = BoundedTranscriptionWorker(
        LocalSpeechProcessor(FakeTranscriber(open_error=OSError("model unreadable"))),
        poll_seconds=0.005,
    )
    worker.start(occurred_at_monotonic_ns=10, packet_sequence=0)
    _wait_for_state(worker, TranscriptionWorkerState.FAILED)
    results = worker.drain()

    submission = worker.submit(
        _utterance(),
        occurred_at_monotonic_ns=20,
        packet_sequence=1,
    )
    worker.stop(timeout_seconds=2.0)

    assert any(
        event.event == "audio.transcription.worker_failed"
        for result in results
        for event in result.events
    )
    assert submission.accepted is False
    assert submission.events[0].event == "audio.transcription.unavailable"
    assert "model unreadable" in str(submission.events[0].payload["reason"])
