from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic_ns
from typing import Callable

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.voice.speech_processor import LocalSpeechProcessor, SpeechProcessingResult
from velvet_audio_studio.voice.utterance import VoiceUtterance


SOURCE_ID = "audio.transcription_worker"


class TranscriptionWorkerState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    STOPPING = "stopping"


@dataclass(frozen=True)
class TranscriptionWorkItem:
    utterance: VoiceUtterance
    packet_sequence: int


@dataclass(frozen=True)
class TranscriptionSubmission:
    accepted: bool
    events: tuple[RuntimeAudioEvent, ...]
    queue_depth: int


@dataclass(frozen=True)
class TranscriptionWorkerResult:
    processing: SpeechProcessingResult | None
    events: tuple[RuntimeAudioEvent, ...]


class BoundedTranscriptionWorker:
    def __init__(
        self,
        processor: LocalSpeechProcessor,
        *,
        queue_capacity: int = 4,
        clock_ns: Callable[[], int] = monotonic_ns,
        poll_seconds: float = 0.02,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.processor = processor
        self.queue_capacity = queue_capacity
        self.clock_ns = clock_ns
        self.poll_seconds = poll_seconds
        self._queue: Queue[TranscriptionWorkItem] = Queue(maxsize=queue_capacity)
        self._results: Queue[TranscriptionWorkerResult] = Queue()
        self._stop_requested = Event()
        self._lock = Lock()
        self._state = TranscriptionWorkerState.STOPPED
        self._thread: Thread | None = None
        self._error: str | None = None
        self._last_sequence = 0

    @property
    def state(self) -> TranscriptionWorkerState:
        with self._lock:
            return self._state

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def start(self, *, occurred_at_monotonic_ns: int, packet_sequence: int) -> tuple[RuntimeAudioEvent, ...]:
        with self._lock:
            if self._state is not TranscriptionWorkerState.STOPPED:
                raise RuntimeError("transcription worker is already started")
            self._state = TranscriptionWorkerState.STARTING
        self._error = None
        self._last_sequence = packet_sequence
        self._stop_requested.clear()
        self._thread = Thread(target=self._run, name="velvet-audio-transcription", daemon=True)
        self._thread.start()
        return (self._event("audio.transcription.worker_starting", occurred_at_monotonic_ns, packet_sequence, {"queue_capacity": self.queue_capacity}),)

    def submit(self, utterance: VoiceUtterance, *, occurred_at_monotonic_ns: int, packet_sequence: int) -> TranscriptionSubmission:
        if self.state not in {TranscriptionWorkerState.STARTING, TranscriptionWorkerState.RUNNING}:
            event = self._event("audio.transcription.unavailable", occurred_at_monotonic_ns, packet_sequence, {"utterance_id": utterance.utterance_id, "reason": self._error or self.state.value, "raw_samples_in_event": False})
            return TranscriptionSubmission(False, (event,), self.queue_depth)
        try:
            self._queue.put_nowait(TranscriptionWorkItem(utterance, packet_sequence))
        except Full:
            event = self._event("audio.transcription.queue_full", occurred_at_monotonic_ns, packet_sequence, {"utterance_id": utterance.utterance_id, "queue_capacity": self.queue_capacity, "raw_samples_in_event": False})
            return TranscriptionSubmission(False, (event,), self.queue_depth)
        self._last_sequence = packet_sequence
        event = self._event("audio.transcription.queued", occurred_at_monotonic_ns, packet_sequence, {"utterance_id": utterance.utterance_id, "queue_depth": self.queue_depth, "raw_samples_in_event": False})
        return TranscriptionSubmission(True, (event,), self.queue_depth)

    def drain(self) -> tuple[TranscriptionWorkerResult, ...]:
        values: list[TranscriptionWorkerResult] = []
        while True:
            try:
                values.append(self._results.get_nowait())
            except Empty:
                return tuple(values)

    def stop(self, *, timeout_seconds: float = 10.0) -> tuple[TranscriptionWorkerResult, ...]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        thread = self._thread
        if thread is None or self.state is TranscriptionWorkerState.STOPPED:
            return self.drain()
        with self._lock:
            self._state = TranscriptionWorkerState.STOPPING
        self._stop_requested.set()
        thread.join(timeout_seconds)
        values = list(self.drain())
        if thread.is_alive():
            values.append(TranscriptionWorkerResult(None, (self._event("audio.transcription.worker_stop_timeout", self.clock_ns(), self._last_sequence, {"timeout_seconds": timeout_seconds}),)))
        return tuple(values)

    def _run(self) -> None:
        opened = False
        try:
            try:
                self.processor.open()
                opened = True
                with self._lock:
                    if self._state is TranscriptionWorkerState.STARTING:
                        self._state = TranscriptionWorkerState.RUNNING
                self._results.put(TranscriptionWorkerResult(None, (self._event("audio.transcription.worker_ready", self.clock_ns(), self._last_sequence, {"queue_capacity": self.queue_capacity}),)))
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                with self._lock:
                    self._state = TranscriptionWorkerState.FAILED
                self._results.put(TranscriptionWorkerResult(None, (self._event("audio.transcription.worker_failed", self.clock_ns(), self._last_sequence, {"error": self._error}),)))
            while not self._stop_requested.is_set() or not self._queue.empty():
                try:
                    item = self._queue.get(timeout=self.poll_seconds)
                except Empty:
                    continue
                try:
                    self._process(item)
                finally:
                    self._queue.task_done()
        finally:
            close_error: str | None = None
            if opened:
                try:
                    self.processor.close()
                except Exception as exc:
                    close_error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._state = TranscriptionWorkerState.STOPPED
            events: list[RuntimeAudioEvent] = []
            if close_error:
                events.append(self._event("audio.transcription.worker_close_error", self.clock_ns(), self._last_sequence, {"error": close_error}))
            events.append(self._event("audio.transcription.worker_stopped", self.clock_ns(), self._last_sequence, {"startup_error": self._error, "queue_depth": self.queue_depth}))
            self._results.put(TranscriptionWorkerResult(None, tuple(events)))

    def _process(self, item: TranscriptionWorkItem) -> None:
        if self._error:
            event = self._event("audio.transcription.failed", self.clock_ns(), item.packet_sequence, {"utterance_id": item.utterance.utterance_id, "error": self._error, "raw_samples_in_event": False})
            self._results.put(TranscriptionWorkerResult(None, (event,)))
            return
        result = self.processor.process(item.utterance, occurred_at_monotonic_ns=self.clock_ns(), packet_sequence=item.packet_sequence)
        self._results.put(TranscriptionWorkerResult(result, result.events))

    @staticmethod
    def _event(name: str, occurred_at_monotonic_ns: int, packet_sequence: int, payload: dict[str, object]) -> RuntimeAudioEvent:
        return RuntimeAudioEvent(event=name, source_id=SOURCE_ID, occurred_at_monotonic_ns=occurred_at_monotonic_ns, packet_sequence=packet_sequence, payload=payload)
