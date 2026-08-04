from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from velvet_audio_studio.capture.microphone_capture import CapturePacket


class CaptureSessionState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    ACTIVE = "active"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class CaptureTransition:
    event: str
    previous_state: CaptureSessionState
    current_state: CaptureSessionState
    reason: str
    packet_sequence: int


class CaptureSession:
    """Tracks one continuous multichannel microphone session."""

    def __init__(self, *, recovery_packets_required: int = 2) -> None:
        if recovery_packets_required <= 0:
            raise ValueError("recovery_packets_required must be positive")
        self.state = CaptureSessionState.STOPPED
        self.packet_sequence = 0
        self._healthy_recovery_packets = 0
        self._recovery_packets_required = recovery_packets_required

    def start(self) -> CaptureTransition:
        if self.state is not CaptureSessionState.STOPPED:
            raise RuntimeError("session is already running")
        previous = self.state
        self.state = CaptureSessionState.STARTING
        self.packet_sequence = 0
        self._healthy_recovery_packets = 0
        return CaptureTransition("audio.capture.starting", previous, self.state, "session requested", 0)

    def observe(self, packet: CapturePacket) -> tuple[CaptureTransition, ...]:
        if self.state is CaptureSessionState.STOPPED:
            raise RuntimeError("session is not running")

        self.packet_sequence += 1
        reasons = tuple(packet.degraded_reasons)
        transitions: list[CaptureTransition] = []

        if not reasons:
            if self.state is CaptureSessionState.STARTING:
                transitions.append(self._change(CaptureSessionState.ACTIVE, "audio.capture.active", "first healthy packet"))
            elif self.state is CaptureSessionState.DEGRADED:
                self._healthy_recovery_packets += 1
                if self._healthy_recovery_packets >= self._recovery_packets_required:
                    transitions.append(self._change(CaptureSessionState.ACTIVE, "audio.capture.recovered", f"{self._healthy_recovery_packets} consecutive healthy packets"))
                    self._healthy_recovery_packets = 0
        else:
            self._healthy_recovery_packets = 0
            if self.state is not CaptureSessionState.DEGRADED:
                transitions.append(self._change(CaptureSessionState.DEGRADED, "audio.capture.degraded", "; ".join(reasons)))

        return tuple(transitions)

    def stop(self) -> CaptureTransition:
        if self.state is CaptureSessionState.STOPPED:
            raise RuntimeError("session is already stopped")
        previous = self.state
        self.state = CaptureSessionState.STOPPED
        self._healthy_recovery_packets = 0
        return CaptureTransition("audio.capture.stopped", previous, self.state, "session stopped", self.packet_sequence)

    def _change(self, target: CaptureSessionState, event: str, reason: str) -> CaptureTransition:
        previous = self.state
        self.state = target
        return CaptureTransition(event, previous, target, reason, self.packet_sequence)
