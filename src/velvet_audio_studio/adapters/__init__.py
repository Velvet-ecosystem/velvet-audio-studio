"""Hardware adapters for Velvet Audio Studio."""

from .base import AudioHardwareAdapter, AudioHardwareHealth
from .audio_injector_octo import AudioInjectorOctoAdapter
from .raspberry_pi import RaspberryPiAudioHost

__all__ = [
    "AudioHardwareAdapter",
    "AudioHardwareHealth",
    "AudioInjectorOctoAdapter",
    "RaspberryPiAudioHost",
]
