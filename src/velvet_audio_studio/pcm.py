"""Hardware-neutral PCM conversion helpers shared by speech input and output."""

from __future__ import annotations

from array import array
from math import isfinite
import sys
from typing import Sequence

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat


def resample_linear(
    samples: Sequence[float],
    *,
    source_rate_hz: int,
    target_rate_hz: int,
) -> tuple[float, ...]:
    if source_rate_hz <= 0 or target_rate_hz <= 0:
        raise ValueError("sample rates must be positive")
    normalized = tuple(float(sample) for sample in samples)
    if any(not isfinite(sample) for sample in normalized):
        raise ValueError("audio samples must be finite")
    if not normalized or source_rate_hz == target_rate_hz:
        return normalized

    output_length = max(1, round(len(normalized) * target_rate_hz / source_rate_hz))
    scale = source_rate_hz / target_rate_hz
    output: list[float] = []
    last_index = len(normalized) - 1
    for output_index in range(output_length):
        source_position = min(output_index * scale, last_index)
        left = int(source_position)
        right = min(left + 1, last_index)
        fraction = source_position - left
        output.append(
            normalized[left] * (1.0 - fraction) + normalized[right] * fraction
        )
    return tuple(output)


def decode_pcm16_le(payload: bytes) -> tuple[float, ...]:
    if len(payload) % 2:
        raise ValueError("S16_LE payload contains a partial sample")
    pcm = array("h")
    pcm.frombytes(payload)
    if sys.byteorder != "little":
        pcm.byteswap()
    return tuple(sample / 32_768.0 for sample in pcm)


def encode_pcm16_le(samples: Sequence[float]) -> bytes:
    pcm = array(
        "h",
        (_integer_sample(sample, AlsaPcmFormat.S16_LE) for sample in samples),
    )
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def encode_routed_mono(
    samples: Sequence[float],
    *,
    total_channels: int,
    output_channels: Sequence[int],
    sample_format: AlsaPcmFormat,
) -> bytes:
    """Duplicate one mono stream into selected slots of an interleaved output bus."""
    if total_channels <= 0:
        raise ValueError("total_channels must be positive")
    selected = tuple(int(channel) for channel in output_channels)
    if not selected:
        raise ValueError("at least one output channel is required")
    if len(set(selected)) != len(selected):
        raise ValueError("output channels must be unique")
    if any(channel < 0 or channel >= total_channels for channel in selected):
        raise ValueError("output channel index is outside the playback bus")
    selected_set = frozenset(selected)

    typecode = "h" if sample_format is AlsaPcmFormat.S16_LE else "i"
    pcm = array(typecode)
    for sample in samples:
        value = _integer_sample(sample, sample_format)
        for channel in range(total_channels):
            pcm.append(value if channel in selected_set else 0)
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def _integer_sample(sample: float, sample_format: AlsaPcmFormat) -> int:
    value = float(sample)
    if not isfinite(value):
        raise ValueError("audio samples must be finite")
    clipped = max(-1.0, min(1.0, value))
    return int(round(clipped * sample_format.maximum_integer))
