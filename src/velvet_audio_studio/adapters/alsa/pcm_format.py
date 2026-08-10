"""Shared ALSA PCM sample-format contract."""

from __future__ import annotations

from enum import StrEnum


class AlsaPcmFormat(StrEnum):
    S16_LE = "S16_LE"
    S32_LE = "S32_LE"

    @property
    def bytes_per_sample(self) -> int:
        return 2 if self is AlsaPcmFormat.S16_LE else 4

    @property
    def normalizer(self) -> float:
        return 32_768.0 if self is AlsaPcmFormat.S16_LE else 2_147_483_648.0

    @property
    def maximum_integer(self) -> int:
        return 32_767 if self is AlsaPcmFormat.S16_LE else 2_147_483_647

    @property
    def struct_format(self) -> str:
        return "<h" if self is AlsaPcmFormat.S16_LE else "<i"
