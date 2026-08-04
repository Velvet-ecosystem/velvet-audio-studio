from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Sequence


@dataclass(frozen=True)
class MixInput:
    samples: Sequence[float]
    output_channels: tuple[int, ...]
    gain: float = 1.0


class MultichannelMixer:
    """Small deterministic mixer for simulation and tests.

    Samples are interleaved by frame. Each MixInput is mono and may be routed to
    one or more zero-based output channels. The hardware backend will later
    consume the resulting interleaved buffer as one multichannel PCM stream.
    """

    def __init__(self, output_channels: int = 8, *, limiter: float = 1.0) -> None:
        if output_channels <= 0:
            raise ValueError("output_channels must be positive")
        if not 0 < limiter <= 1.0:
            raise ValueError("limiter must be in the range (0, 1]")
        self.output_channels = output_channels
        self.limiter = limiter

    def mix(self, inputs: Iterable[MixInput], *, frames: int) -> list[float]:
        if frames < 0:
            raise ValueError("frames cannot be negative")
        output = [0.0] * (frames * self.output_channels)

        for source in inputs:
            if len(source.samples) != frames:
                raise ValueError("every source must contain exactly one mono sample per frame")
            if not isfinite(source.gain):
                raise ValueError("gain must be finite")
            if not source.output_channels:
                raise ValueError("source must route to at least one output channel")
            for channel in source.output_channels:
                if channel < 0 or channel >= self.output_channels:
                    raise ValueError(f"output channel {channel} is out of range")

            for frame, sample in enumerate(source.samples):
                if not isfinite(sample):
                    raise ValueError("samples must be finite")
                value = sample * source.gain
                base = frame * self.output_channels
                for channel in source.output_channels:
                    output[base + channel] += value

        return [max(-self.limiter, min(self.limiter, sample)) for sample in output]
