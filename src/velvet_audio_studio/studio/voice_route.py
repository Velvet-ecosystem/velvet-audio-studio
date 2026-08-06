"""Logical routing for Velvet's primary spoken voice."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceRoute:
    route_id: str
    owner: str
    source_name: str
    output_channels: tuple[int, ...]
    gain: float
    priority: int
    ducks_priorities_below: int
    duck_gain: float

    def __post_init__(self) -> None:
        if not self.route_id:
            raise ValueError("route_id is required")
        if not self.output_channels:
            raise ValueError("at least one output channel is required")
        if any(channel < 0 or channel > 7 for channel in self.output_channels):
            raise ValueError("output channels must be zero-based Octo channels 0..7")
        if not 0.0 <= self.gain <= 1.0:
            raise ValueError("gain must be between 0.0 and 1.0")
        if not 0.0 <= self.duck_gain <= 1.0:
            raise ValueError("duck_gain must be between 0.0 and 1.0")


def default_velvet_voice_route() -> VoiceRoute:
    """Return the initial Tiburon voice route.

    The center voice output is preferred, with the front pair providing a
    restrained supporting image. Physical wiring remains configurable later.
    """

    return VoiceRoute(
        route_id="velvet.voice.primary",
        owner="velvet",
        source_name="velvet_tts",
        output_channels=(0, 1, 4),
        gain=0.85,
        priority=80,
        ducks_priorities_below=80,
        duck_gain=0.25,
    )
