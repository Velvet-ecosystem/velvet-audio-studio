"""Audio Injector Octo adapter package and stable public exports."""

from __future__ import annotations

from velvet_audio_studio.adapters.base import AudioHardwareHealth

from .alsa_capture import (
    AlsaCaptureConfig,
    AlsaCaptureError,
    AlsaOctoCaptureSource,
    AlsaPcmFormat,
    decode_interleaved_pcm,
)
from .capture_factory import (
    OctoCaptureResolution,
    OctoCaptureUnavailable,
    resolve_octo_capture,
    stable_alsa_pcm_name,
)
from .channel_map import DEFAULT_TIBURON_MAP, OctoChannelMap


class AudioInjectorOctoAdapter:
    """Hardware-neutral Octo boundary retained from the initial scaffold."""

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

    def apply_route(
        self,
        input_channels: tuple[int, ...],
        output_channels: tuple[int, ...],
    ) -> None:
        raise NotImplementedError("Octo routing backend is not connected")

    def set_gain(self, output_channels: tuple[int, ...], gain_db: float) -> None:
        raise NotImplementedError("Octo mixer backend is not connected")

    def release_route(self, request_id: str) -> None:
        self._routes.pop(request_id, None)


__all__ = [
    "AlsaCaptureConfig",
    "AlsaCaptureError",
    "AlsaOctoCaptureSource",
    "AlsaPcmFormat",
    "AudioInjectorOctoAdapter",
    "DEFAULT_TIBURON_MAP",
    "OctoCaptureResolution",
    "OctoCaptureUnavailable",
    "OctoChannelMap",
    "decode_interleaved_pcm",
    "resolve_octo_capture",
    "stable_alsa_pcm_name",
]
