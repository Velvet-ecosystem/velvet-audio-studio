from __future__ import annotations

from dataclasses import asdict, dataclass
from time import monotonic_ns
from typing import Sequence

from velvet_audio_studio.capture.microphone_capture import (
    VoiceCapturePacket,
    analyze_capture,
)
from velvet_audio_studio.capture.session import CaptureSession, CaptureTransition
from velvet_audio_studio.capture.voice_handoff import VoiceInputHandoff, prepare_voice_handoff


@dataclass(frozen=True)
class RuntimeAudioEvent:
    event: str
    source_id: str
    occurred_at_monotonic_ns: int
    packet_sequence: int
    payload: dict[str, object]


@dataclass(frozen=True)
class CaptureSupervisorResult:
    packet: VoiceCapturePacket
    handoff: VoiceInputHandoff
    events: tuple[RuntimeAudioEvent, ...]


class CaptureSupervisor:
    """Coordinates capture analysis, lifecycle, handoff, and Runtime events."""

    def __init__(self, *, recovery_packets_required: int = 2) -> None:
        self.session = CaptureSession(
            recovery_packets_required=recovery_packets_required,
        )

    def start(self, *, occurred_at_monotonic_ns: int | None = None) -> RuntimeAudioEvent:
        transition = self.session.start()
        return self._transition_event(transition, occurred_at_monotonic_ns)

    def process(
        self,
        interleaved_samples: Sequence[float],
        *,
        sample_rate_hz: int = 48_000,
        muted_channels: frozenset[int] = frozenset(),
        captured_at_monotonic_ns: int | None = None,
        observed_at_monotonic_ns: int | None = None,
    ) -> CaptureSupervisorResult:
        packet = analyze_capture(
            interleaved_samples,
            sample_rate_hz=sample_rate_hz,
            muted_channels=muted_channels,
            captured_at_monotonic_ns=captured_at_monotonic_ns,
            observed_at_monotonic_ns=observed_at_monotonic_ns,
        )
        transitions = self.session.observe(packet)
        handoff = prepare_voice_handoff(packet)
        occurred_ns = (
            monotonic_ns()
            if observed_at_monotonic_ns is None
            else observed_at_monotonic_ns
        )

        events: list[RuntimeAudioEvent] = [
            RuntimeAudioEvent(
                event=packet.event,
                source_id=packet.source_id,
                occurred_at_monotonic_ns=occurred_ns,
                packet_sequence=self.session.packet_sequence,
                payload={
                    "sample_rate_hz": packet.sample_rate_hz,
                    "frames": packet.frames,
                    "stale": packet.stale,
                    "degraded_reasons": packet.degraded_reasons,
                    "channels": tuple(asdict(channel) for channel in packet.channels),
                },
            )
        ]
        events.extend(
            self._transition_event(transition, occurred_ns)
            for transition in transitions
        )
        events.append(
            RuntimeAudioEvent(
                event=handoff.event,
                source_id=packet.source_id,
                occurred_at_monotonic_ns=occurred_ns,
                packet_sequence=self.session.packet_sequence,
                payload={
                    "selected_channel_index": handoff.selected_channel_index,
                    "selected_logical_name": handoff.selected_logical_name,
                    "confidence": handoff.confidence,
                    "frames": len(handoff.mono_samples),
                    "degraded_reasons": handoff.degraded_reasons,
                },
            )
        )
        return CaptureSupervisorResult(packet, handoff, tuple(events))

    def stop(self, *, occurred_at_monotonic_ns: int | None = None) -> RuntimeAudioEvent:
        transition = self.session.stop()
        return self._transition_event(transition, occurred_at_monotonic_ns)

    @staticmethod
    def _transition_event(
        transition: CaptureTransition,
        occurred_at_monotonic_ns: int | None,
    ) -> RuntimeAudioEvent:
        occurred_ns = (
            monotonic_ns()
            if occurred_at_monotonic_ns is None
            else occurred_at_monotonic_ns
        )
        return RuntimeAudioEvent(
            event=transition.event,
            source_id="octo.capture.primary",
            occurred_at_monotonic_ns=occurred_ns,
            packet_sequence=transition.packet_sequence,
            payload={
                "previous_state": transition.previous_state.value,
                "current_state": transition.current_state.value,
                "reason": transition.reason,
            },
        )
