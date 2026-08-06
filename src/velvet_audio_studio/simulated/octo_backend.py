"""Simulated six-input/eight-output backend for studio development."""

from __future__ import annotations

from velvet_audio_studio.adapters.contracts import AudioCapability, AudioDeviceAdapter, DeviceHealth


class SimulatedOctoAdapter(AudioDeviceAdapter):
    adapter_id = "simulated-octo"

    def probe(self) -> AudioCapability:
        return AudioCapability(
            adapter_id=self.adapter_id,
            device_identity="simulated:octo",
            playback_channels=8,
            capture_channels=6,
            sample_rates_hz=(44_100, 48_000),
            sample_formats=("S16_LE", "S24_LE", "S32_LE"),
            health=DeviceHealth.ACCEPTED,
        )

    def open_playback(self) -> object:
        return object()

    def open_capture(self) -> object:
        return object()

    def close(self) -> None:
        return None
