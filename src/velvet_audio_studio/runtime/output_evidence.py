"""Audio-output lifecycle evidence for the shared durable Runtime event path."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Lock
from time import monotonic_ns
from uuid import uuid4

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent
from velvet_audio_studio.contracts import AudioPriority

AUDIO_OUTPUT_CONTRACT = "velvet.audio-output-evidence.v1"
AUDIO_OUTPUT_SCHEMA_VERSION = "1.0"

AUDIO_OUTPUT_BOOKED = "audio.output.booked"
AUDIO_OUTPUT_STARTED = "audio.output.started"
AUDIO_OUTPUT_COMPLETED = "audio.output.completed"
AUDIO_OUTPUT_PREEMPTED = "audio.output.preempted"
AUDIO_OUTPUT_FAILED = "audio.output.failed"
AUDIO_OUTPUT_RECOVERED = "audio.output.recovered"

_FIXED_FLAGS = {
    "evidence_only": True,
    "authority": "none",
    "grants_authority": False,
    "grants_execution": False,
    "grants_actuation": False,
    "audio_output_only": True,
}

PublishEvents = Callable[[Sequence[RuntimeAudioEvent]], object]


@dataclass(frozen=True)
class EvidencePublishFailure:
    output_event_id: str
    event_type: str
    reason: str


class AudioOutputEvidenceEmitter:
    """Create privacy-bounded output events and send them through Runtime ordering.

    Publishing evidence must not become an audio-authority gate. The supplied
    publisher is expected to be the Studio durable Runtime pipeline. If that
    boundary itself raises, the failure is retained locally for health/status
    reporting while the safety-relevant audio operation is allowed to continue.
    """

    def __init__(
        self,
        *,
        node_id: str,
        publish_events: PublishEvents,
        source_id: str = "octo.playback.primary",
        clock_ns: Callable[[], int] = monotonic_ns,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        if not node_id.strip():
            raise ValueError("audio output evidence node_id cannot be empty")
        if not source_id.strip():
            raise ValueError("audio output evidence source_id cannot be empty")
        self.node_id = node_id.strip()
        self.source_id = source_id.strip()
        self.publish_events = publish_events
        self.clock_ns = clock_ns
        self.id_factory = id_factory
        self._sequence = 0
        self._lock = Lock()
        self._publish_failures: list[EvidencePublishFailure] = []

    @property
    def publish_failures(self) -> tuple[EvidencePublishFailure, ...]:
        with self._lock:
            return tuple(self._publish_failures)

    def booked(
        self,
        *,
        request_id: str,
        priority: AudioPriority,
        output_channels: tuple[int, ...],
        expression_id: str | None,
        profile_id: str,
        model_id: str,
        displaced_request_ids: tuple[str, ...] = (),
    ) -> RuntimeAudioEvent:
        return self._emit(
            AUDIO_OUTPUT_BOOKED,
            request_id=request_id,
            priority=priority,
            output_channels=output_channels,
            expression_id=expression_id,
            profile_id=profile_id,
            model_id=model_id,
            extra={"displaced_request_ids": list(displaced_request_ids)},
        )

    def started(
        self,
        *,
        request_id: str,
        priority: AudioPriority,
        output_channels: tuple[int, ...],
        expression_id: str | None,
        profile_id: str,
        model_id: str,
        source_sample_rate_hz: int,
        playback_sample_rate_hz: int,
        source_frames: int,
    ) -> RuntimeAudioEvent:
        return self._emit(
            AUDIO_OUTPUT_STARTED,
            request_id=request_id,
            priority=priority,
            output_channels=output_channels,
            expression_id=expression_id,
            profile_id=profile_id,
            model_id=model_id,
            extra={
                "source_sample_rate_hz": source_sample_rate_hz,
                "playback_sample_rate_hz": playback_sample_rate_hz,
                "source_frames": source_frames,
            },
        )

    def completed(
        self,
        *,
        request_id: str,
        priority: AudioPriority,
        output_channels: tuple[int, ...],
        expression_id: str | None,
        profile_id: str,
        model_id: str,
        playback_sample_rate_hz: int,
        frames_written: int,
        playback_duration_ms: float,
    ) -> RuntimeAudioEvent:
        return self._emit(
            AUDIO_OUTPUT_COMPLETED,
            request_id=request_id,
            priority=priority,
            output_channels=output_channels,
            expression_id=expression_id,
            profile_id=profile_id,
            model_id=model_id,
            extra={
                "playback_sample_rate_hz": playback_sample_rate_hz,
                "frames_written": frames_written,
                "playback_duration_ms": playback_duration_ms,
            },
        )

    def preempted(
        self,
        *,
        request_id: str,
        priority: AudioPriority,
        output_channels: tuple[int, ...],
        expression_id: str | None,
        profile_id: str,
        model_id: str,
        playback_sample_rate_hz: int,
        frames_written: int,
        playback_duration_ms: float,
        preempted_by_request_id: str,
    ) -> RuntimeAudioEvent:
        return self._emit(
            AUDIO_OUTPUT_PREEMPTED,
            request_id=request_id,
            priority=priority,
            output_channels=output_channels,
            expression_id=expression_id,
            profile_id=profile_id,
            model_id=model_id,
            extra={
                "playback_sample_rate_hz": playback_sample_rate_hz,
                "frames_written": frames_written,
                "playback_duration_ms": playback_duration_ms,
                "preempted_by_request_id": preempted_by_request_id,
            },
        )

    def failed(
        self,
        *,
        request_id: str,
        priority: AudioPriority,
        output_channels: tuple[int, ...],
        expression_id: str | None,
        profile_id: str,
        model_id: str | None,
        failure_stage: str,
        error: BaseException,
    ) -> RuntimeAudioEvent:
        # Do not serialize exception text here. Some engines include the input
        # sentence in error messages, which would duplicate private speech into
        # operational evidence. The stable class and stage are enough for the
        # canonical receipt; detailed diagnostics belong in a protected local log.
        error_class = type(error).__name__
        return self._emit(
            AUDIO_OUTPUT_FAILED,
            request_id=request_id,
            priority=priority,
            output_channels=output_channels,
            expression_id=expression_id,
            profile_id=profile_id,
            model_id=model_id,
            extra={
                "failure_stage": failure_stage,
                "error_class": error_class,
                "reason": f"{failure_stage} failed: {error_class}",
                "recovery_required": True,
            },
        )

    def recovered(
        self,
        *,
        request_id: str,
        priority: AudioPriority,
        output_channels: tuple[int, ...],
        expression_id: str | None,
        profile_id: str,
        model_id: str,
        recovered_from_event_id: str,
        recovered_from_stage: str,
    ) -> RuntimeAudioEvent:
        return self._emit(
            AUDIO_OUTPUT_RECOVERED,
            request_id=request_id,
            priority=priority,
            output_channels=output_channels,
            expression_id=expression_id,
            profile_id=profile_id,
            model_id=model_id,
            extra={
                "recovered_from_event_id": recovered_from_event_id,
                "recovered_from_stage": recovered_from_stage,
            },
        )

    def _emit(
        self,
        event_type: str,
        *,
        request_id: str,
        priority: AudioPriority,
        output_channels: tuple[int, ...],
        expression_id: str | None,
        profile_id: str,
        model_id: str | None,
        extra: dict[str, object],
    ) -> RuntimeAudioEvent:
        if not request_id.strip():
            raise ValueError("audio output evidence request_id cannot be empty")
        if len(set(output_channels)) != len(output_channels):
            raise ValueError("audio output evidence channels must be unique")
        if any(channel < 0 for channel in output_channels):
            raise ValueError("audio output evidence channels cannot be negative")

        output_event_id = self.id_factory()
        if not output_event_id.strip():
            raise ValueError("audio output evidence id factory returned an empty id")
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        payload: dict[str, object] = {
            "schema_version": AUDIO_OUTPUT_SCHEMA_VERSION,
            "output_event_id": output_event_id,
            "request_id": request_id.strip(),
            "node_id": self.node_id,
            "priority": int(priority),
            "output_channels": list(output_channels),
            **_FIXED_FLAGS,
            **extra,
        }
        if expression_id is not None and expression_id.strip():
            payload["expression_id"] = expression_id.strip()
        if profile_id.strip():
            payload["profile_id"] = profile_id.strip()
        if model_id is not None and model_id.strip():
            payload["model_id"] = model_id.strip()

        event = RuntimeAudioEvent(
            event=event_type,
            source_id=self.source_id,
            occurred_at_monotonic_ns=self.clock_ns(),
            packet_sequence=sequence,
            payload=payload,
        )
        try:
            self.publish_events((event,))
        except Exception as exc:
            failure = EvidencePublishFailure(
                output_event_id=output_event_id,
                event_type=event_type,
                reason=(
                    "Runtime evidence publish failed: "
                    f"{type(exc).__name__}: {' '.join(str(exc).split())[:384]}"
                ),
            )
            with self._lock:
                self._publish_failures.append(failure)
        return event
