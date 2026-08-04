"""Audio Injector Octo adapter boundary.

The initial adapter deliberately avoids shelling directly into ALSA. Device
probing and mixer commands will be implemented behind an injectable backend so
tests and simulated-body runs use the same studio contracts.
"""

from __future__ import annotations

from .base import AudioHardwareHealth


class AudioInjectorOctoAdapter:
    EXPECTED_INPUTS = 6
    EXPECTED_OUTPUTS = 8

    def __init__(self, device_name: str = "AudioInjectorOcto") -> None:
        self.device_name = device_name
        self._routes: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}

    def discover(self) -> AudioHardwareHealth:
        return AudioHardwareHealth(
            available=False,
            input_channels=self.EXPECTED_INPUTS,
            output_channels=self.EXPECTED_OUTPUTS,
            device_name=self.device_name,
            degraded_reason="ALSA discovery backend not connected",
        )

    def apply_route(self, input_channels: tuple[int, ...], output_channels: tuple[int, ...]) -> None:
        raise NotImplementedError("Octo routing backend is not connected")

    def set_gain(self, output_channels: tuple[int, ...], gain_db: float) -> None:
        raise NotImplementedError("Octo mixer backend is not connected")

    def release_route(self, request_id: str) -> None:
        self._routes.pop(request_id, None)
