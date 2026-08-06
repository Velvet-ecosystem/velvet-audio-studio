"""Logical-to-physical channel mapping for the Audio Injector Octo.

The codec is exposed as one multichannel PCM stream. Logical studio routes are
translated into zero-based TDM/ALSA slot positions here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OctoChannelMap:
    outputs: tuple[str, ...]
    inputs: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.outputs) != 8:
            raise ValueError("Audio Injector Octo requires exactly 8 output slots")
        if len(self.inputs) != 6:
            raise ValueError("Audio Injector Octo requires exactly 6 input slots")
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError("Output channel names must be unique")
        if len(set(self.inputs)) != len(self.inputs):
            raise ValueError("Input channel names must be unique")

    def output_slot(self, logical_name: str) -> int:
        return self.outputs.index(logical_name)

    def input_slot(self, logical_name: str) -> int:
        return self.inputs.index(logical_name)


DEFAULT_TIBURON_MAP = OctoChannelMap(
    outputs=(
        "front_left",
        "front_right",
        "rear_left",
        "rear_right",
        "center_voice",
        "subwoofer",
        "external_alert",
        "spare_aux",
    ),
    inputs=(
        "driver_upper_mic",
        "passenger_upper_mic",
        "rear_left_mic",
        "rear_right_mic",
        "center_roof_mic",
        "service_aux",
    ),
)
