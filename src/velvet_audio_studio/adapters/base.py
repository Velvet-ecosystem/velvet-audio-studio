"""Abstract boundary between studio policy and audio hardware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AudioHardwareHealth:
    available: bool
    input_channels: int
    output_channels: int
    device_name: str
    degraded_reason: str | None = None


class AudioHardwareAdapter(Protocol):
    def discover(self) -> AudioHardwareHealth:
        ...

    def apply_route(self, input_channels: tuple[int, ...], output_channels: tuple[int, ...]) -> None:
        ...

    def set_gain(self, output_channels: tuple[int, ...], gain_db: float) -> None:
        ...

    def release_route(self, request_id: str) -> None:
        ...
