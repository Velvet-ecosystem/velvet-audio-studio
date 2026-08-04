from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from time import monotonic_ns
from typing import Sequence


MICROPHONE_NAMES = (
    "driver_upper_mic",
    "passenger_upper_mic",
    "rear_left_mic",
    "rear_right_mic",
    "center_roof_mic",
    "service_aux",
)


@dataclass(frozen=True)
class ChannelLevel:
    channel_index: int
    logical_name: str
    peak: float
    rms: float
    clipped: bool
    muted: bool


@dataclass(frozen=True)
class VoiceCapturePacket:
    event: str
    source_id: str
    sample_rate_hz: int
    frames: int
    captured_at_monotonic_ns: int
    stale_after_ms: int
    stale: bool
    channels: tuple[ChannelLevel, ...]
    interleaved_samples: tuple[float, ...]
    degraded_reasons: tuple[str, ...] = ()


def analyze_capture(
    interleaved_samples: Sequence[float],
    *,
    sample_rate_hz: int = 48_000,
    channel_names: Sequence[str] = MICROPHONE_NAMES,
    muted_channels: frozenset[int] = frozenset(),
    clipping_threshold: float = 0.98,
    stale_after_ms: int = 250,
    captured_at_monotonic_ns: int | None = None,
    observed_at_monotonic_ns: int | None = None,
) -> VoiceCapturePacket:
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if not channel_names:
        raise ValueError("at least one capture channel is required")
    if not 0 < clipping_threshold <= 1.0:
        raise ValueError("clipping_threshold must be in the range (0, 1]")
    if stale_after_ms <= 0:
        raise ValueError("stale_after_ms must be positive")
    if len(interleaved_samples) % len(channel_names) != 0:
        raise ValueError("interleaved sample count must divide evenly by channel count")

    samples = tuple(float(sample) for sample in interleaved_samples)
    if any(not isfinite(sample) for sample in samples):
        raise ValueError("capture samples must be finite")

    captured_ns = monotonic_ns() if captured_at_monotonic_ns is None else captured_at_monotonic_ns
    observed_ns = monotonic_ns() if observed_at_monotonic_ns is None else observed_at_monotonic_ns
    stale = observed_ns - captured_ns > stale_after_ms * 1_000_000
    frames = len(samples) // len(channel_names)

    levels: list[ChannelLevel] = []
    reasons: list[str] = []
    for channel_index, logical_name in enumerate(channel_names):
        channel_samples = samples[channel_index::len(channel_names)]
        muted = channel_index in muted_channels
        effective = (0.0,) * len(channel_samples) if muted else channel_samples
        peak = max((abs(sample) for sample in effective), default=0.0)
        rms = sqrt(sum(sample * sample for sample in effective) / len(effective)) if effective else 0.0
        clipped = peak >= clipping_threshold
        if clipped:
            reasons.append(f"{logical_name} clipping")
        levels.append(
            ChannelLevel(
                channel_index=channel_index,
                logical_name=logical_name,
                peak=peak,
                rms=rms,
                clipped=clipped,
                muted=muted,
            )
        )

    if stale:
        reasons.append("capture packet stale")
    if frames == 0:
        reasons.append("capture packet empty")

    return VoiceCapturePacket(
        event="audio.capture.packet",
        source_id="octo.capture.primary",
        sample_rate_hz=sample_rate_hz,
        frames=frames,
        captured_at_monotonic_ns=captured_ns,
        stale_after_ms=stale_after_ms,
        stale=stale,
        channels=tuple(levels),
        interleaved_samples=samples,
        degraded_reasons=tuple(reasons),
    )
