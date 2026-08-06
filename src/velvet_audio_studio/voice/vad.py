from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite, sqrt
from typing import Sequence


class VoiceActivityState(StrEnum):
    SILENT = "silent"
    ACTIVE = "active"


@dataclass(frozen=True)
class VoiceActivityConfig:
    activation_rms: float = 0.03
    deactivation_rms: float = 0.015
    activation_packets: int = 2
    release_packets: int = 3

    def __post_init__(self) -> None:
        for name, value in (
            ("activation_rms", self.activation_rms),
            ("deactivation_rms", self.deactivation_rms),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            if not isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.deactivation_rms > self.activation_rms:
            raise ValueError("deactivation_rms cannot exceed activation_rms")
        if self.activation_packets <= 0:
            raise ValueError("activation_packets must be positive")
        if self.release_packets <= 0:
            raise ValueError("release_packets must be positive")


@dataclass(frozen=True)
class VoiceActivityDecision:
    state: VoiceActivityState
    event: str
    rms: float
    peak: float
    sample_count: int
    consecutive_activation_packets: int
    consecutive_release_packets: int


class EnergyVoiceActivityDetector:
    """Packet-level energy detector with hysteresis and debounce."""

    def __init__(self, config: VoiceActivityConfig = VoiceActivityConfig()) -> None:
        self.config = config
        self.state = VoiceActivityState.SILENT
        self._activation_packets = 0
        self._release_packets = 0

    def process(self, samples: Sequence[float]) -> VoiceActivityDecision:
        normalized = tuple(float(sample) for sample in samples)
        if any(not isfinite(sample) for sample in normalized):
            raise ValueError("voice activity samples must be finite")

        peak = max((abs(sample) for sample in normalized), default=0.0)
        rms = (
            sqrt(sum(sample * sample for sample in normalized) / len(normalized))
            if normalized
            else 0.0
        )

        if self.state is VoiceActivityState.SILENT:
            self._release_packets = 0
            self._activation_packets = (
                self._activation_packets + 1
                if rms >= self.config.activation_rms
                else 0
            )
            if self._activation_packets >= self.config.activation_packets:
                self.state = VoiceActivityState.ACTIVE
                self._activation_packets = 0
                event = "audio.voice_activity.started"
            else:
                event = "audio.voice_activity.silent"
        else:
            self._activation_packets = 0
            self._release_packets = (
                self._release_packets + 1
                if rms <= self.config.deactivation_rms
                else 0
            )
            if self._release_packets >= self.config.release_packets:
                self.state = VoiceActivityState.SILENT
                self._release_packets = 0
                event = "audio.voice_activity.ended"
            else:
                event = "audio.voice_activity.active"

        return VoiceActivityDecision(
            state=self.state,
            event=event,
            rms=rms,
            peak=peak,
            sample_count=len(normalized),
            consecutive_activation_packets=self._activation_packets,
            consecutive_release_packets=self._release_packets,
        )

    def reset(self) -> VoiceActivityState:
        previous = self.state
        self.state = VoiceActivityState.SILENT
        self._activation_packets = 0
        self._release_packets = 0
        return previous
