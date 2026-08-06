"""Signal handling that lets the service runner publish an ordered shutdown."""

from __future__ import annotations

from contextlib import contextmanager
import signal
from types import FrameType
from typing import Iterator


class ShutdownSignalLatch:
    """Convert SIGINT and SIGTERM into a polling-safe shutdown request."""

    def __init__(self) -> None:
        self.requested = False
        self.signal_number: int | None = None
        self._previous: dict[int, signal.Handlers] = {}

    def request(self, signum: int, _frame: FrameType | None = None) -> None:
        self.requested = True
        self.signal_number = signum

    def is_requested(self) -> bool:
        return self.requested

    @contextmanager
    def installed(self) -> Iterator[ShutdownSignalLatch]:
        if self._previous:
            raise RuntimeError("shutdown signal latch is already installed")
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self.request)
        try:
            yield self
        finally:
            for signum, previous in self._previous.items():
                signal.signal(signum, previous)
            self._previous.clear()
