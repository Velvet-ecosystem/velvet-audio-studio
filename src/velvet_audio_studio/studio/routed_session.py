"""First end-to-end logical studio session for Velvet's voice."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from velvet_audio_studio.engine.multichannel_mixer import MixInput, MultichannelMixer
from velvet_audio_studio.studio.ducking import ActiveSource, DuckDecision, apply_ducking
from velvet_audio_studio.studio.voice_route import VoiceRoute, default_velvet_voice_route


@dataclass(frozen=True)
class RoutedSessionReceipt:
    event: str
    route_id: str
    owner: str
    output_channels: tuple[int, ...]
    frames: int
    priority: int
    ducking: tuple[DuckDecision, ...]

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["ducking"] = [asdict(decision) for decision in self.ducking]
        return payload


def render_velvet_voice(
    voice_samples: tuple[float, ...],
    *,
    background_samples: tuple[float, ...] | None = None,
    route: VoiceRoute | None = None,
) -> tuple[list[float], RoutedSessionReceipt]:
    route = route or default_velvet_voice_route()
    frames = len(voice_samples)
    if background_samples is not None and len(background_samples) != frames:
        raise ValueError("background and voice must have the same frame count")

    active = [ActiveSource(route.source_name, route.priority, route.gain)]
    if background_samples is not None:
        active.append(ActiveSource("music", 30, 1.0))

    decisions = apply_ducking(
        tuple(active),
        authority_source_id=route.source_name,
        threshold=route.ducks_priorities_below,
        duck_gain=route.duck_gain,
    )
    gains = {decision.source_id: decision.applied_gain for decision in decisions}

    inputs = [
        MixInput(
            samples=voice_samples,
            output_channels=route.output_channels,
            gain=gains[route.source_name],
        )
    ]
    if background_samples is not None:
        inputs.append(
            MixInput(
                samples=background_samples,
                output_channels=(0, 1, 2, 3),
                gain=gains["music"],
            )
        )

    output = MultichannelMixer(output_channels=8, limiter=1.0).mix(inputs, frames=frames)
    receipt = RoutedSessionReceipt(
        event="audio.session.started",
        route_id=route.route_id,
        owner=route.owner,
        output_channels=route.output_channels,
        frames=frames,
        priority=route.priority,
        ducking=decisions,
    )
    return output, receipt
