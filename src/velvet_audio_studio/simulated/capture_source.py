"""Deterministic six-channel capture source for service development."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import replace
from time import monotonic_ns
from typing import Callable

from velvet_audio_studio.runtime.service_runner import CaptureFrame


class SimulatedCaptureSource:
    """Concrete CaptureSource backed by scripted frames, idle reads, or failures.

    Items are consumed in order. ``None`` represents an idle poll and an
    ``Exception`` is raised from ``read`` so service recovery paths can be tested
    without special hooks in the runner.
    """

    def __init__(
        self,
        items: Iterable[CaptureFrame | None | Exception] = (),
        *,
        clock_ns: Callable[[], int] = monotonic_ns,
        stamp_zero_timestamps: bool = True,
    ) -> None:
        self._items = deque(items)
        self.clock_ns = clock_ns
        self.stamp_zero_timestamps = stamp_zero_timestamps
        self.is_open = False
        self.open_count = 0
        self.close_count = 0
        self.read_count = 0

    @property
    def pending_items(self) -> int:
        return len(self._items)

    def append(self, item: CaptureFrame | None | Exception) -> None:
        self._items.append(item)

    def extend(self, items: Iterable[CaptureFrame | None | Exception]) -> None:
        self._items.extend(items)

    def open(self) -> None:
        if self.is_open:
            raise RuntimeError("simulated capture source is already open")
        self.is_open = True
        self.open_count += 1

    def read(self) -> CaptureFrame | None:
        if not self.is_open:
            raise RuntimeError("simulated capture source is closed")
        self.read_count += 1
        if not self._items:
            return None

        item = self._items.popleft()
        if isinstance(item, Exception):
            raise item
        if item is None:
            return None
        if self.stamp_zero_timestamps and item.captured_at_monotonic_ns == 0:
            return replace(item, captured_at_monotonic_ns=self.clock_ns())
        return item

    def close(self) -> None:
        if not self.is_open:
            return
        self.is_open = False
        self.close_count += 1


def simulated_six_channel_frame(
    interleaved_samples: Iterable[float],
    *,
    captured_at_monotonic_ns: int = 0,
    sample_rate_hz: int = 48_000,
    muted_channels: frozenset[int] = frozenset(),
) -> CaptureFrame:
    """Build a validated six-channel frame without hiding interleaving errors."""
    samples = tuple(float(sample) for sample in interleaved_samples)
    if len(samples) % 6:
        raise ValueError("simulated Octo capture must contain complete six-channel frames")
    return CaptureFrame(
        interleaved_samples=samples,
        captured_at_monotonic_ns=captured_at_monotonic_ns,
        sample_rate_hz=sample_rate_hz,
        muted_channels=muted_channels,
    )
