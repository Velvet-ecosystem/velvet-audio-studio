"""Hardware-neutral contracts for audio devices."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DeviceHealth(StrEnum):
    ACCEPTED = "accepted"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AudioCapability:
    adapter_id: str
    device_identity: str
    playback_channels: int
    capture_channels: int
    sample_rates_hz: tuple[int, ...]
    sample_formats: tuple[str, ...]
    health: DeviceHealth
    degraded_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        return self.health in {DeviceHealth.ACCEPTED, DeviceHealth.DEGRADED}


class AudioDeviceAdapter:
    """Boundary implemented by Octo, simulated, and future audio units."""

    adapter_id: str

    def probe(self) -> AudioCapability:
        raise NotImplementedError

    def open_playback(self) -> object:
        raise NotImplementedError

    def open_capture(self) -> object:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
